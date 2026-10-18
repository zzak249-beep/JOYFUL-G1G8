import logging
import os
import signal
import threading
import time
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

import config as C
import features
import ledger as ledger_mod
import messages
import stats
import strategy
import universe
import v9
from commands import command_loop
from meta import Brain
from notifier import notify, send_document
from store import Seen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

BOOKS = {0: "Base", 1: "V2 (filtros)", 2: "V3 (+momentum)", 3: "V4 (+HaP)"}
STATUS = {"started": time.time(), "last_scan": None, "symbols": 0}


def fetch_all(trader, sym):
    df = trader.fetch_closed(sym, C.TF, 500)
    htf = {t: (df if t == C.TF else trader.fetch_closed(sym, t, 300)) for t in features.needed_tfs()}
    return df, htf


def paper_events(brain, ledger, sym, df, silent=False):
    """Avanza las operaciones virtuales y avisa de cada hito (TP1, SL, TP2...)."""
    for ev, tr in ledger.update(sym, df):
        learn = tr.get("mode") == "virtual_blocked"       # señal bloqueada por una puerta: aprende en silencio
        if silent and not learn:      # sombra en modo real: solo mide; lo real ya avisa y aprende por su cuenta
            continue
        if not learn:
            notify(messages.event_message(tr, ev, tr.get("R"), tr.get("exit_px")))
        if tr["closed"] and tr.get("x") and tr.get("R") is not None and ev != "EXPIRED":
            brain.record({"x": tr["x"], "y": int(tr["R"] > 0), "r": tr["R"], "hour": tr["hour"],
                          "wday": tr["wday"], "code": tr["kinds"][0], "src": "blocked" if learn else "virtual", "ts": time.time()})


def scan_symbol(trader, brain, ledger, sym, cls, bar_id, now, last_bar):
    """Devuelve (score, sym, sig, info, mult) si hay señal válida; None si no."""
    df = trader.fetch_closed(sym, C.TF, 120)
    expected = (bar_id - 1) * C.TF_SEC * 1000
    if len(df) < 60 or int(df.index[-1].timestamp() * 1000) != expected:
        if now % C.TF_SEC > 120:
            last_bar[sym] = bar_id   # mercado cerrado / sin datos nuevos
        return None
    last_bar[sym] = bar_id
    if ledger.has_open(sym):
        paper_events(brain, ledger, sym, df, silent=not C.DRY_RUN)
    if sym in trader.state:
        trader.trail_update(sym, float(df.close.iloc[-1]))
        return None
    if C.DRY_RUN and ledger.has_open(sym, "virtual"):
        return None
    close_ts = df.index[-1] + pd.Timedelta(seconds=C.TF_SEC)
    if not universe.market_open(cls, close_ts):
        return None
    cand = v9.has_candidate(df) if C.STRATEGY == "v9" else strategy.has_candidate(features.build_light(df))
    if not cand:
        return None
    df, htf = fetch_all(trader, sym)
    F = features.build(df, htf, trader.funding_hist(sym) if (C.FUNDING_ON or C.FUND_PCT_ON) else None)
    if C.OI_ON:
        F["oi_chg"][-1] = trader.oi_change(sym)
    F["tradfi"] = universe.is_tradfi(cls)
    sig = strategy.signal_at(F, len(df) - 1, C.BOOK)
    if not sig:
        return None
    ok, info, mult, p = brain.check(sig)
    if not ok:
        log.info("Señal bloqueada %s %s: %s", sym, sig["code"], info)
        if C.LEARN_BLOCKED and ledger.count_open("virtual_blocked") < C.MAX_LEARN:
            tr = ledger_mod.make_paper(sig, sym, trader.ex.market(sym)["id"], trader.prec(sym))
            tr["mode"] = "virtual_blocked"
            ledger.add(tr)
        return None
    return (p if p is not None else float(F["rvol"][-1]), sym, sig, info, mult)


def main():
    from trader import Trader
    brain = Brain()
    trader = Trader(notify, brain)
    ledger = ledger_mod.Ledger(os.path.join(C.DATA_DIR, f"{C.PREFIX}_ledger.json"))
    trader.ledger = ledger
    seen = Seen(os.path.join(C.DATA_DIR, f"{C.PREFIX}_seen.json"))
    last_bar, cls_of, syms, last_uni, halt_day, last_summary = {}, {}, [], 0, None, None
    g = brain.gate

    def open_list():
        out = [{"side": t["side"], "symbol": t["symbol"], "tf": t["tf"], "kinds": t["kinds"], "entry": t["entry"],
                "sl": t["sl"], "stage": t["stage"], "bars": t["bars"], "prec": t["prec"], "mode": "virtual"}
               for t in ledger.open if t.get("mode") == "virtual"]
        out += [{"side": st["side"].upper(), "symbol": st.get("sym_id", s), "tf": C.TF, "kinds": [st["code"]],
                 "entry": st["entry"], "sl": st["sl"], "stage": 2 if st.get("tp1_hit") else 1,
                 "bars": int((time.time() - st["opened"]) / C.TF_SEC), "prec": st.get("prec", 6), "mode": "real"}
                for s, st in trader.state.items()]
        return out

    def stats_msg(days=None, title="📊 Estadísticas"):
        return stats.stats_message(ledger, len(open_list()), days, title)

    def status_msg():
        ls = "aún sin escanear" if not STATUS["last_scan"] else f"hace {int((time.time() - STATUS['last_scan']) / 60)} min"
        return (f"✅ Activo hace {(time.time() - STATUS['started']) / 3600:.1f} h · {STATUS['symbols']} símbolos · "
                f"último escaneo {ls} · {'SOLO SEÑALES' if C.DRY_RUN else 'REAL'}")

    notify(
        f"✅ <b>Granville Bot iniciado</b> · TF {C.TF} · estrategia {'V9 (crowding+adaptativo)' if C.STRATEGY == 'v9' else 'G1-G8 · libro ' + str(BOOKS.get(C.BOOK))}\n"
        f"Modo: {'SOLO SEÑALES (seguimiento virtual)' if C.DRY_RUN else ('DEMO' if C.DEMO else 'DINERO REAL')}\n"
        f"Universo: {'todo BingX (' + ','.join(C.CLASSES) + ')' if C.SCAN_ALL else ', '.join(C.SYMBOLS)} · "
        f"riesgo {C.RISK_PCT}% · máx. posiciones {C.MAX_POSITIONS if not C.DRY_RUN else C.MAX_OPEN_PAPER}\n"
        f"Meta: {'ON' if C.META_ON else 'OFF'} ({'modelo n=' + str(g.n) if g.beta is not None else 'sin modelo'}) · "
        f"Estac.: {'ON' if C.SEASON_ON else 'OFF'} · Trailing: {'ON' if C.TRAIL_ON else 'OFF'} · "
        f"Muestras: {len(brain.samples)} · abiertas: {len(open_list())}\n"
        f"Comandos: /stats /semana /abiertas /estado /pausa /reanudar /cerrartodo /exportar")

    stop = threading.Event()
    for _sig in (signal.SIGTERM, signal.SIGINT):      # Railway envía SIGTERM al redesplegar: cerrar rápido y limpio
        try:
            signal.signal(_sig, lambda *_: stop.set())
        except Exception:
            pass
    if C.COMMANDS_ON and C.TG_TOKEN and C.TG_CHAT:
        handlers = {"/stats": lambda: notify(stats_msg()), "/estadisticas": lambda: notify(stats_msg()),
                    "/semana": lambda: notify(stats_msg(7)), "/week": lambda: notify(stats_msg(7)),
                    "/abiertas": lambda: notify(messages.open_message(open_list())),
                    "/open": lambda: notify(messages.open_message(open_list())),
                    "/pausa": lambda: (trader.set_paused(True), notify("⏸ Pausado: no abro operaciones nuevas (las abiertas siguen gestionadas).")),
                    "/reanudar": lambda: (trader.set_paused(False), notify("▶️ Reanudado.")),
                    "/exportar": lambda: (ledger.save(), send_document(ledger.path, f"Historial de {C.BOT_NAME} para compare.py")),
                    "/cerrartodo": lambda: notify("🧯 Esto cierra a mercado TODAS las operaciones del bot y lo pausa. "
                                                  "Para confirmar envía /confirmarcierre"),
                    "/confirmarcierre": lambda: notify(f"🧯 Cierre total: {trader.close_all()} operaciones cerradas. Bot en pausa (/reanudar para seguir)."),
                    "/estado": lambda: notify(status_msg()), "/status": lambda: notify(status_msg()),
                    "/ayuda": lambda: notify("Comandos: /stats (todo) · /semana (7 días) · /abiertas · /estado"),
                    "/start": lambda: notify("Comandos: /stats (todo) · /semana (7 días) · /abiertas · /estado")}
        threading.Thread(target=command_loop, args=(C.TG_TOKEN, C.TG_CHAT, handlers, stop, lambda: notify(
            "⚠️ Los comandos (/stats, /abiertas…) no funcionan: otro proceso usa este mismo bot de Telegram "
            "(u hay un webhook activo). Las señales y operaciones siguen normales. Solución: un bot de BotFather "
            "exclusivo para este servicio, o TELEGRAM_COMMANDS=false.")), daemon=True).start()

    if not C.DRY_RUN:
        try:
            trader.check_account()
        except Exception as e:
            C.DRY_RUN = True
            notify(f"🚨 No pude conectar con BingX ({e}). Revisa API key/secret y permisos. Paso a SOLO SEÑALES.")

    while not stop.is_set():
        try:
            now = time.time()
            if C.SCAN_ALL and now - last_uni > C.UNIVERSE_REFRESH:
                try:
                    u = universe.build(trader.ex)
                    syms, cls_of, last_uni = [s for s, _, _ in u], {s: c for s, c, _ in u}, now
                    notify(f"🔎 Universo: {len(u)} símbolos {dict(Counter(cls_of.values()))}")
                except Exception as e:
                    notify(f"⚠️ No pude construir el universo: {e}")
                    last_uni = now - C.UNIVERSE_REFRESH + 300
            elif not C.SCAN_ALL and not syms:
                syms = C.SYMBOLS
                cls_of = {s: universe.classify(trader.ex.market(s)) for s in syms}
            STATUS["symbols"] = len(syms)
            scan = list(dict.fromkeys(syms + list(trader.state.keys()) + ledger.open_symbols()))

            bar_id = int(now // C.TF_SEC)
            for sym in list(trader.state.keys()):
                trader.manage(sym)

            if now % C.TF_SEC >= 4:
                found = []
                for sym in scan:
                    if stop.is_set():
                        break
                    if last_bar.get(sym) == bar_id:
                        continue
                    try:
                        r = scan_symbol(trader, brain, ledger, sym, cls_of.get(sym, "crypto"), bar_id, now, last_bar)
                        if r:
                            found.append(r)
                    except Exception as e:
                        log.warning("Error en %s: %s", sym, e)
                for tr_msg in ledger.expire_stale(int(now * 1000)):
                    if tr_msg[1].get("mode") != "virtual_blocked":
                        notify(messages.event_message(tr_msg[1], "EXPIRED"))
                if found or any(last_bar.get(s) == bar_id for s in scan):
                    STATUS["last_scan"] = time.time()

                found.sort(key=lambda r: -r[0])
                new = []
                for item in found:
                    key = f"{item[1]}|{C.TF}|{item[2]['bar_ts']}|{item[2]['side']}"
                    if not seen.seen(key):
                        new.append((key, item))
                limit = C.MAX_SIGNALS_PER_SCAN
                skipped = []
                for key, (score, sym, sig, info, mult) in new:
                    seen.add(key)
                    name = f"{sym.split('/')[0]} {sig['side']} {sig['code']}"
                    if limit <= 0:
                        skipped.append(name)
                        continue
                    sid, prec = trader.ex.market(sym)["id"], trader.prec(sym)
                    if C.DRY_RUN:
                        if ledger.count_open("virtual") >= C.MAX_OPEN_PAPER or \
                                sum(1 for t_ in ledger.open if t_["side"] == sig["side"].upper()) >= C.MAX_SAME_DIR:
                            skipped.append(name)
                            continue
                        notify(messages.signal_message(sig, sid, prec, C.ACCOUNT_USDT, False, int(time.time() * 1000), info))
                        ledger.add(ledger_mod.make_paper(sig, sym, sid, prec))
                        limit -= 1
                    else:
                        if not trader.can_open():
                            if halt_day != trader.day:
                                halt_day = trader.day
                                notify(f"⛔ Freno diario: pérdida ≥ {C.DAILY_LOSS_PCT}% del capital hoy. "
                                       f"No se abren más operaciones hasta mañana (UTC).")
                            continue
                        same_dir = sum(1 for st in trader.state.values() if st["side"] == sig["side"])
                        if same_dir >= C.MAX_SAME_DIR:
                            skipped.append(f"{name} (ya hay {same_dir} {sig['side']})")
                            continue
                        if trader.has_position(sym) or len(trader.state) >= C.MAX_POSITIONS or not trader.spread_ok(sym, sig["risk"] / sig["entry"] * 100):
                            skipped.append(name)
                            continue
                        try:
                            acct = trader._equity()
                        except Exception:
                            acct = C.ACCOUNT_USDT
                        notify(messages.signal_message(sig, sid, prec, acct, True, int(time.time() * 1000), info))
                        if C.SHADOW_ON:
                            ledger.add(ledger_mod.make_paper(sig, sym, sid, prec))
                        trader.open_trade(sym, sig, mult)
                        limit -= 1
                if skipped:
                    notify(f"➕ {len(skipped)} señales más omitidas por el límite de señales/posiciones abiertas: "
                           + ", ".join(skipped[:15]))
                seen.save()
                ledger.save()

            utc = datetime.now(timezone.utc)
            if 0 <= C.SUMMARY_HOUR <= 23 and utc.hour == C.SUMMARY_HOUR and last_summary != utc.date():
                last_summary = utc.date()
                notify(stats_msg(1, "🗓 Resumen diario") + "\n\n" + stats_msg(None, "Acumulado"))
        except Exception as e:
            log.exception("Error en bucle")
            notify(f"⚠️ Error en bucle principal: {e}")
            stop.wait(30)
        stop.wait(10)
    ledger.save()
    seen.save()
    notify("⏹ Bot detenido (redespliegue o parada). Los SL/TP ya colocados siguen activos en BingX.")


if __name__ == "__main__":
    main()
