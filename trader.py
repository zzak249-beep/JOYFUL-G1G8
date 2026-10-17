"""Ejecución en BingX (futuros perpetuos USDT) vía ccxt.
Entrada a mercado + SL en el exchange + TP1 (50%) + TP2 (50%) o trailing ATR tras TP1.
Cada operación cerrada se registra (R neto) y alimenta el meta-modelo y la estacionalidad."""
import json
import logging
import math
import os
import time

import ccxt
import pandas as pd

import config as C
import messages
from store import load_state

log = logging.getLogger("trader")


class Trader:
    def __init__(self, notify, brain=None):
        self.notify = notify
        self.brain = brain
        self.ex = ccxt.bingx({
            "apiKey": C.BINGX_KEY, "secret": C.BINGX_SECRET,
            "enableRateLimit": True, "options": {"defaultType": "swap"},
        })
        if C.DEMO:
            self.ex.set_sandbox_mode(True)
        self.ex.rateLimit = max(self.ex.rateLimit or 0, C.RATE_LIMIT_MS)
        self.ex.load_markets()
        self.state = self._load()
        self.day, self.day_pnl = None, 0.0
        self.ledger = None
        self.health_path = os.path.join(C.DATA_DIR, f"{C.PREFIX}_health.json")
        self.health = {"slip": [], "paused": False}
        try:
            with open(self.health_path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.health.update({k: d[k] for k in ("slip", "paused") if k in d})
        except Exception:
            pass
        self.size_mult = 1.0

    # ---------- estado ----------
    def _load(self):
        return load_state(C.STATE_PATH)

    def _save(self):
        try:
            os.makedirs(C.DATA_DIR, exist_ok=True)
            with open(C.STATE_PATH, "w") as f:
                json.dump(self.state, f)
        except Exception as e:
            log.warning("No se pudo guardar estado: %s", e)

    # ---------- utilidades ----------
    def _retry(self, fn, *a, **k):
        for n in range(4):
            try:
                return fn(*a, **k)
            except Exception as e:
                if n < 3 and ("100410" in str(e) or "RateLimit" in type(e).__name__ or "DDoS" in type(e).__name__):
                    time.sleep(2 * (n + 1))
                    continue
                raise

    def _roll(self):
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if d != self.day:
            self.day, self.day_pnl = d, 0.0

    def can_open(self):
        self._roll()
        return not self.health.get("paused") and self.day_pnl > -C.DAILY_LOSS_PCT

    def _save_health(self):
        try:
            os.makedirs(C.DATA_DIR, exist_ok=True)
            with open(self.health_path, "w") as f:
                json.dump(self.health, f)
        except Exception:
            pass

    def set_paused(self, flag):
        self.health["paused"] = bool(flag)
        self._save_health()

    def check_kill(self):
        """Kill-switch: si lo REAL rinde mucho peor que lo VIRTUAL (sombra) en las últimas N operaciones, pausa."""
        if not self.ledger or self.health.get("paused"):
            return
        real = [r["R"] for r in self.ledger.closed if r.get("mode") == "real"][-C.KILL_MIN_N:]
        virt = [r["R"] for r in self.ledger.closed if r.get("mode") == "virtual"][-C.KILL_MIN_N:]
        if len(real) < C.KILL_MIN_N or len(virt) < C.KILL_MIN_N:
            return
        gap = sum(virt) / len(virt) - sum(real) / len(real)
        if gap > C.KILL_GAP_R:
            self.set_paused(True)
            self.notify(f"🚨 <b>Kill-switch</b>: lo real rinde {sum(real) / len(real):+.2f}R/op y lo virtual "
                        f"{sum(virt) / len(virt):+.2f}R/op (brecha {gap:.2f}R en las últimas {C.KILL_MIN_N}). "
                        f"No abro más. Revisa slippage/comisiones y usa /reanudar cuando quieras seguir.")

    def check_account(self):
        try:
            foreign = [p["symbol"] for p in self.ex.fetch_positions()
                       if float(p.get("contracts") or 0) > 0 and p["symbol"] not in self.state]
            if foreign:
                self.notify(f"⚠️ Hay {len(foreign)} posiciones abiertas que NO son de este bot ({', '.join(foreign[:6])}). "
                            f"Si otro bot opera en esta cuenta, los resultados de ambos NO serán independientes: usa una subcuenta propia por bot.")
        except Exception:
            pass
        eq = self._equity()
        self.notify(f"✅ Cuenta BingX conectada · equity {eq:.2f} USDT · modo posición: {C.POSITION_MODE}")
        return eq

    def prec(self, sym):
        try:
            p = float(self.ex.market(sym).get("precision", {}).get("price"))
        except Exception:
            return 6
        return int(round(-math.log10(p))) if 0 < p < 1 else int(p)

    def _view(self, sym, st):
        pl = st.get("plan") or {}
        return {"side": st["side"].upper(), "symbol": st.get("sym_id") or sym, "tf": C.TF, "kinds": [st["code"]],
                "entry": st["entry"], "tp2": pl.get("tp2") or st.get("tp_final") or st["entry"],
                "prec": st.get("prec", 6), "trail_atr": st.get("trail_atr", C.TRAIL_ATR), "bars": int((time.time() - st["opened"]) / C.TF_SEC)}

    # ---------- datos ----------
    def fetch_closed(self, symbol, tf, limit):
        raw = self._retry(self.ex.fetch_ohlcv, symbol, tf, limit=limit)
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df.index = pd.to_datetime(df.ts, unit="ms", utc=True)
        tf_ms = self.ex.parse_timeframe(tf) * 1000
        if df.ts.iloc[-1] + tf_ms > time.time() * 1000:
            df = df.iloc[:-1]
        return df.drop(columns="ts").astype(float)

    def spread_ok(self, sym, risk_pct=None):
        """Spread máximo: C.MAX_SPREAD_PCT y, si se da el riesgo (% del precio), MAX_SPREAD_R x riesgo."""
        try:
            tk = self.ex.fetch_ticker(sym)
            b, a = tk.get("bid"), tk.get("ask")
            if not (b and a):
                return True
            spr = (a - b) / ((a + b) / 2) * 100
            lim = C.MAX_SPREAD_PCT if not risk_pct else min(C.MAX_SPREAD_PCT, C.MAX_SPREAD_R * risk_pct)
            return spr <= lim
        except Exception:
            return True

    def funding_hist(self, sym):
        """Historial de funding (~100 pagos) en caché de 1 h, como Serie indexada por hora."""
        cache = getattr(self, "_fh", {})
        self._fh = cache
        t = time.time()
        if sym in cache and t - cache[sym][0] < 3600:
            return cache[sym][1]
        try:
            rows = self._retry(self.ex.fetch_funding_rate_history, sym, limit=100)
            s = pd.Series({pd.to_datetime(r["timestamp"], unit="ms", utc=True): float(r["fundingRate"]) for r in rows}).sort_index()
            cache[sym] = (t, s if len(s) else None)
        except Exception:
            cache[sym] = (t, None)
        return cache[sym][1]

    def oi_change(self, sym):
        """Cambio relativo del Open Interest en las últimas OI_WINDOW_H horas (histórico propio: BingX solo da el valor actual)."""
        path = os.path.join(C.DATA_DIR, f"{C.PREFIX}_oi.json")
        hist = getattr(self, "_oi", None)
        if hist is None:
            try:
                with open(path) as f:
                    hist = json.load(f)
            except Exception:
                hist = {}
            self._oi = hist
        try:
            o = self.ex.fetch_open_interest(sym)
            val = float(o.get("openInterestAmount") or o.get("openInterestValue"))
        except Exception:
            return float("nan")
        now = time.time()
        h = [x for x in hist.get(sym, []) if now - x[0] < 48 * 3600] + [[now, val]]
        hist[sym] = h[-200:]
        try:
            os.makedirs(C.DATA_DIR, exist_ok=True)
            with open(path, "w") as f:
                json.dump(hist, f)
        except Exception:
            pass
        old = [x for x in h if now - x[0] >= C.OI_WINDOW_H * 3600 * 0.9]
        return (val / old[-1][1] - 1.0) if old and old[-1][1] > 0 else float("nan")

    def funding(self, sym):
        try:
            return float(self.ex.fetch_funding_rate(sym).get("fundingRate"))
        except Exception:
            return None

    # ---------- helpers ----------
    def _p(self, side, closing=False, extra=None):
        p = {}
        if C.POSITION_MODE == "hedge":
            p["positionSide"] = "LONG" if side == "long" else "SHORT"
        else:
            p["positionSide"] = "BOTH"
            if closing:
                p["reduceOnly"] = True
        if extra:
            p.update(extra)
        return p

    def _amt(self, sym, x): return float(self.ex.amount_to_precision(sym, x))
    def _px(self, sym, x): return float(self.ex.price_to_precision(sym, x))

    def _equity(self):
        b = self.ex.fetch_balance()
        u = b.get("USDT", {})
        return float(u.get("total") or u.get("free") or 0)

    def pos_qty(self, sym, side=None):
        try:
            for p in self.ex.fetch_positions([sym]):
                if p.get("symbol") == sym and (side is None or p.get("side") == side):
                    q = float(p.get("contracts") or 0)
                    if q > 0:
                        return q
        except Exception as e:
            log.warning("fetch_positions: %s", e)
        return 0.0

    def has_position(self, sym):
        return sym in self.state or self.pos_qty(sym) > 0

    def _stop(self, sym, side, qty, price):
        side_c = "sell" if side == "long" else "buy"
        try:
            o = self.ex.create_order(sym, "market", side_c, qty, None,
                                     self._p(side, True, {"stopLossPrice": price}))
        except Exception as e1:
            log.warning("SL unificado falló (%s), probando STOP_MARKET", e1)
            o = self.ex.create_order(sym, "STOP_MARKET", side_c, qty, None,
                                     self._p(side, True, {"stopPrice": price}))
        return o["id"]

    def _tp(self, sym, side, qty, price):
        """Take profit nativo (disparador TAKE_PROFIT_MARKET, igual que el SL). Respaldo: orden límite."""
        side_c = "sell" if side == "long" else "buy"
        errs = []

        def trig1(): return self.ex.create_order(sym, "market", side_c, qty, None, self._p(side, True, {"takeProfitPrice": price}))
        def trig2(): return self.ex.create_order(sym, "TAKE_PROFIT_MARKET", side_c, qty, None, self._p(side, True, {"stopPrice": price}))
        def lim(): return self.ex.create_order(sym, "limit", side_c, qty, price, self._p(side, True))
        for fn in ((lim, trig1, trig2) if C.TP_ORDER == "limit" else (trig1, trig2, lim)):
            try:
                return fn()["id"]
            except Exception as e:
                errs.append(str(e)[:140])
        raise Exception(" | ".join(errs))

    def _is_open(self, sym, oid):
        try:
            return self.ex.fetch_order(oid, sym).get("status") == "open"
        except Exception:
            return True   # ante la duda, asumir que existe (evita duplicar)

    def _plan(self, sym, st):
        s = 1 if st["side"] == "long" else -1
        e, r, qty = st["entry"], st["risk"], st["qty"]
        m = self.ex.market(sym)
        min_amt = (m.get("limits", {}).get("amount", {}).get("min") or 0)
        q1 = self._amt(sym, qty * C.TP1_FRAC)
        q2 = self._amt(sym, qty - q1)
        if C.PARTIAL_MODE and q1 >= min_amt and q2 >= min_amt:
            return {"partial": True, "q1": q1, "q2": q2, "tp1": self._px(sym, e + s * C.TP1_R * r),
                    "tp2": None if C.TRAIL_ON else self._px(sym, e + s * st.get("tp2_r", C.TP2_R) * r)}
        return {"partial": False, "q": qty, "tp": self._px(sym, e + s * C.TP_SINGLE_R * r)}

    def _place_tps(self, sym, st):
        """Coloca los TP que falten según el plan. Devuelve (ok, texto, errores)."""
        pl, side, errs = st["plan"], st["side"], []
        if pl["partial"]:
            st["trail"] = bool(C.TRAIL_ON)
            if not st["tp1_hit"] and not st["tp1_id"]:
                try:
                    st["tp1_id"] = self._tp(sym, side, pl["q1"], pl["tp1"])
                except Exception as e:
                    errs.append(f"TP1: {e}")
            if pl["tp2"] and not st["tp2_id"]:
                try:
                    st["tp2_id"] = self._tp(sym, side, pl["q2"], pl["tp2"])
                    st["tp_final"] = pl["tp2"]
                except Exception as e:
                    errs.append(f"TP2: {e}")
            need = ((not st["tp1_hit"] and not st["tp1_id"]) or (pl["tp2"] and not st["tp2_id"]))
            txt = f"TP1 {pl['tp1']} ({pl['q1']})" + (f" · TP2 {pl['tp2']} ({pl['q2']})" if pl["tp2"]
                                                      else f" · resto con trailing {C.TRAIL_ATR}×ATR")
        else:
            st["tp1_done"] = True
            if not st["tp2_id"]:
                try:
                    st["tp2_id"] = self._tp(sym, side, pl["q"], pl["tp"])
                    st["tp_final"] = pl["tp"]
                except Exception as e:
                    errs.append(f"TP: {e}")
            need = not st["tp2_id"]
            txt = f"TP {pl['tp']}"
        st["tp_ok"] = not need
        return st["tp_ok"], txt, errs

    def _cancel(self, sym, oid):
        if oid:
            try:
                self.ex.cancel_order(oid, sym)
            except Exception:
                pass

    def _closed(self, sym, oid):
        try:
            return self.ex.fetch_order(oid, sym).get("status") == "closed"
        except Exception:
            return False

    def _flatten(self, sym, side):
        q = self.pos_qty(sym, side)
        if q > 0:
            side_c = "sell" if side == "long" else "buy"
            self.ex.create_order(sym, "market", side_c, q, None, self._p(side, True))

    # ---------- aprendizaje ----------
    def _record(self, st, exit_price, reason=""):
        """R neto de la operación -> freno diario + cerebro (meta + estacionalidad)."""
        s = 1 if st["side"] == "long" else -1
        risk = st["risk"]
        real = C.TP1_FRAC * C.TP1_R if st.get("tp1_hit") else 0.0
        rem = (1 - C.TP1_FRAC) if st.get("tp1_hit") else 1.0
        R = real + rem * s * (exit_price - st["entry"]) / risk
        Rn = R - C.COST_PCT / (risk / st["entry"] * 100)
        self._roll()
        self.day_pnl += Rn * C.RISK_PCT
        if self.ledger is not None:
            self.ledger.add_closed({"mode": "real", "symbol": st.get("sym_id", ""), "side": st["side"].upper(),
                                    "tf": C.TF, "kinds": [st["code"]], "R": Rn, "reason": reason,
                                    "exit_ms": int(time.time() * 1000),
                                    "bars": int((time.time() - st["opened"]) / C.TF_SEC)})
            self.ledger.save()
        if self.brain and st.get("x"):
            self.brain.record({"x": st["x"], "y": int(Rn > 0), "r": Rn, "hour": st["hour"],
                               "wday": st["wday"], "code": st["code"], "src": "real", "ts": time.time()})
        self.check_kill()
        return Rn

    # ---------- abrir ----------
    def open_trade(self, sym, sig, mult=1.0):
        side = sig["side"]
        m = self.ex.market(sym)
        try:
            self.ex.set_margin_mode("isolated", sym)
        except Exception:
            pass
        try:
            self.ex.set_leverage(C.LEVERAGE, sym,
                                 {"side": ("LONG" if side == "long" else "SHORT") if C.POSITION_MODE == "hedge" else "BOTH"})
        except Exception as e:
            log.warning("set_leverage: %s", e)

        equity = self._equity()
        if equity <= 0:
            self.notify(f"⚠️ {sym}: balance 0, no se opera")
            return
        risk = sig["risk"]
        mult = mult * sig.get("risk_mult", 1.0) * self.size_mult
        qty = equity * C.RISK_PCT / 100.0 * mult / risk
        qty = self._amt(sym, min(qty, equity * C.MAX_LEV / sig["entry"]))
        min_amt = (m.get("limits", {}).get("amount", {}).get("min") or 0)
        min_cost = (m.get("limits", {}).get("cost", {}).get("min") or 0)
        if qty < min_amt or qty * sig["entry"] < min_cost:
            self.notify(f"⚠️ {sym}: tamaño {qty} por debajo del mínimo del exchange; señal omitida")
            return

        o = self.ex.create_order(sym, "market", "buy" if side == "long" else "sell", qty, None, self._p(side))
        fill = o.get("average") or o.get("price")
        if not fill:
            try:
                fill = self.ex.fetch_order(o["id"], sym).get("average")
            except Exception:
                fill = None
        fill = float(fill or sig["entry"])
        slip = (1 if side == "long" else -1) * (fill - sig["entry"]) / risk      # >0 = peor precio que el de la señal
        self.health["slip"] = (self.health.get("slip", []) + [round(slip, 4)])[-20:]
        recent = self.health["slip"][-10:]
        avg_slip = sum(recent) / len(recent)
        if len(recent) >= 5 and avg_slip > C.SLIP_MAX_R and self.size_mult == 1.0:
            self.size_mult = 0.5
            self.notify(f"⚠️ Slippage medio {avg_slip:.2f}R > {C.SLIP_MAX_R}R: reduzco el tamaño al 50% hasta que baje.")
        elif len(recent) >= 5 and avg_slip < C.SLIP_MAX_R * 0.6 and self.size_mult < 1.0:
            self.size_mult = 1.0
            self.notify("✅ Slippage normalizado: tamaño al 100%.")
        self._save_health()
        s = sig["s"]
        sl = self._px(sym, fill - s * risk)

        try:
            sl_id = self._stop(sym, side, qty, sl)
        except Exception as e:
            self._flatten(sym, side)
            self.notify(f"🚨 {sym}: no pude poner el SL ({e}). Posición CERRADA por seguridad.")
            return

        st = {"side": side, "code": sig["code"], "entry": fill, "risk": risk, "qty": qty, "atr": sig["atr"],
              "sl": sl, "sl_id": sl_id, "opened": time.time(), "tp1_id": None, "tp2_id": None,
              "tp1_done": False, "tp1_hit": False, "trail": False, "tp_final": None, "tp_ok": False, "plan": None, "sym_id": m["id"], "prec": self.prec(sym), "trail_atr": sig.get("trail_atr", C.TRAIL_ATR), "tp2_r": sig.get("tp2_r", C.TP2_R), "max_bars": sig.get("max_bars", C.MAX_BARS),
              "x": sig["x"], "hour": sig["hour"], "wday": sig["wday"]}
        st["plan"] = self._plan(sym, st)
        ok, tp_txt, errs = self._place_tps(sym, st)
        if not ok:
            tp_txt += "\n⚠️ TP NO colocado: " + " ; ".join(errs)[:400] + "\n(el bot reintenta cada minuto; el SL sí está activo)"

        self.state[sym] = st
        self._save()
        self.notify(
            f"✅ <b>Orden ejecutada en BingX</b> · {m['id']} · {side.upper()} [{sig['code']}]\n"
            f"Entrada {fill} (slippage {slip:+.2f}R) · cantidad {qty} · SL {sl}\n{tp_txt}\n"
            f"Riesgo {C.RISK_PCT * mult:.2f}% del capital (≈{equity * C.RISK_PCT * mult / 100:.2f} USDT)")

    def close_all(self):
        """Cierra a mercado todo lo que abrió el bot, cancela sus órdenes y pausa. Devuelve cuántas cerró."""
        n = 0
        for sym, st in list(self.state.items()):
            try:
                for k in ("sl_id", "tp1_id", "tp2_id"):
                    self._cancel(sym, st.get(k))
                px = float(self.ex.fetch_ticker(sym)["last"])
                self._flatten(sym, st["side"])
                rn = self._record(st, px, "MANUAL")
                self.notify(f"🧯 Cerrada a mercado · {st.get('sym_id', sym)} [{st['code']}]"
                            + (f" · {rn:+.2f}R neto" if rn is not None else ""))
                self.state.pop(sym, None)
                n += 1
            except Exception as e:
                self.notify(f"🚨 {sym}: no pude cerrar ({e}). Ciérrala a mano en BingX.")
        self._save()
        self.set_paused(True)
        return n

    # ---------- trailing (se llama al cerrar cada vela) ----------
    def trail_update(self, sym, close):
        st = self.state.get(sym)
        if not st or not st.get("trail") or not st.get("tp1_hit"):
            return
        s = 1 if st["side"] == "long" else -1
        new = self._px(sym, close - s * st.get("trail_atr", C.TRAIL_ATR) * st["atr"])
        if s * (new - st["sl"]) <= 0:
            return
        qty = self.pos_qty(sym, st["side"])
        if qty <= 0:
            return
        try:
            new_id = self._stop(sym, st["side"], qty, new)
            self._cancel(sym, st["sl_id"])
            st["sl_id"], st["sl"] = new_id, new
            self._save()
            self.notify(f"📈 Trailing {sym}: SL → {new}")
        except Exception as e:
            self.notify(f"⚠️ {sym}: fallo al mover trailing ({e})")

    # ---------- gestionar ----------
    def manage(self, sym):
        st = self.state.get(sym)
        if not st:
            return
        side = st["side"]
        try:
            open_ids = {o["id"] for o in self.ex.fetch_open_orders(sym)}
            qty_now = self.pos_qty(sym, side)
        except Exception as e:
            log.warning("manage %s: %s", sym, e)
            return

        if qty_now <= 0:
            tp_hit = bool(st["tp2_id"]) and self._closed(sym, st["tp2_id"])
            sl_hit = self._closed(sym, st["sl_id"])
            for k in ("sl_id", "tp1_id", "tp2_id"):
                self._cancel(sym, st.get(k))
            ev, px, rn = None, None, None
            if tp_hit and st.get("tp_final"):
                ev, px = "TP2", st["tp_final"]
            elif sl_hit:
                px = st["sl"]
                ev = "SL" if not st.get("tp1_hit") else ("BE" if abs(st["sl"] - st["entry"]) < 1e-12 else "TRAIL")
            view = self._view(sym, st)
            if ev:
                rn = self._record(st, px, ev)
                self.notify(messages.event_message(view, ev, rn, px))
            else:
                self.notify(f"ℹ️ Posición cerrada · {view['symbol']} [{st['code']}]")
            self.state.pop(sym, None)
            self._save()
            return

        reduced = bool(st.get("plan") and st["plan"].get("partial")) and qty_now <= st["qty"] * (1 - C.TP1_FRAC) * 1.05
        if st["tp1_id"] and not st["tp1_done"] and (reduced or (st["tp1_id"] not in open_ids and self._closed(sym, st["tp1_id"]))):
            st["tp1_done"] = st["tp1_hit"] = True
            self._cancel(sym, st["sl_id"])
            try:
                st["sl_id"] = self._stop(sym, side, qty_now, self._px(sym, st["entry"]))
                st["sl"] = st["entry"]
                self.notify(messages.event_message(self._view(sym, st), "TP1"))
            except Exception as e:
                self.notify(f"🚨 {sym}: TP1 hecho pero fallo al mover SL a BE ({e}). Revisa a mano.")
            self._save()

        # auto-reparación: coloca el TP que falte (también en posiciones abiertas con versiones anteriores)
        if not st.get("tp_ok") and time.time() - st.get("tp_try", 0) > 60:
            st["tp_try"] = time.time()
            if not st.get("plan"):
                st["plan"] = self._plan(sym, st)
                for k in ("tp1_id", "tp2_id"):
                    if st.get(k) and not self._is_open(sym, st[k]) and not (k == "tp1_id" and st["tp1_hit"]):
                        st[k] = None
            ok, txt, errs = self._place_tps(sym, st)
            if ok:
                self.notify(f"🔧 TP colocado en {sym}: {txt}")
            elif not st.get("tp_err_sent"):
                st["tp_err_sent"] = True
                self.notify(f"⚠️ {sym}: sigo sin poder poner el TP: " + " ; ".join(errs)[:400])
            self._save()

        if time.time() - st["opened"] >= st.get("max_bars", C.MAX_BARS) * C.TF_SEC:
            for k in ("sl_id", "tp1_id", "tp2_id"):
                self._cancel(sym, st.get(k))
            try:
                px = float(self.ex.fetch_ticker(sym)["last"])
                self._flatten(sym, side)
                rn = self._record(st, px, "TIME")
                self.notify(messages.event_message(self._view(sym, st), "TIME", rn, px))
                self.state.pop(sym, None)
                self._save()
            except Exception as e:
                self.notify(f"🚨 {sym}: fallo cierre por tiempo ({e})")
