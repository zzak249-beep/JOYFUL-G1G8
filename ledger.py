"""Operaciones virtuales (seguimiento de cada señal en DRY_RUN) + historial de cerradas (virtuales y reales)."""
import json
import os
import threading
import time
import config as C


def _stop_hit(s, bar, level): return bar["low"] <= level if s > 0 else bar["high"] >= level
def _target_hit(s, bar, level): return bar["high"] >= level if s > 0 else bar["low"] <= level


def close_at(tr, price, reason, exit_ms):
    s = 1 if tr["side"] == "LONG" else -1
    rem = 1.0 if tr["stage"] == 1 else 1.0 - tr["p"]
    tr["realized"] += rem * s * (price - tr["entry"]) / tr["risk"]
    tr.update(closed=True, reason=reason, exit_ms=exit_ms, exit_px=price, R=tr["realized"] - tr["cost_r"])


def make_paper(sig, sym, sym_id, prec):
    partial = C.PARTIAL_MODE
    e, risk = sig["entry"], sig["risk"]
    return {"id": f"{sym_id}|{C.TF}|{sig['bar_ts']}|{sig['side']}", "mode": "virtual", "sym": sym, "symbol": sym_id,
            "tf": C.TF, "tf_ms": C.TF_SEC * 1000, "side": sig["side"].upper(), "kinds": [sig["code"]],
            "sig_ms": sig["bar_ts"], "last_ms": sig["bar_ts"], "entry": e, "risk": risk, "sl": sig["sl"],
            "tp1": sig["tp1"] if partial else sig["tp_single"], "tp2": sig["tp2"], "atr": sig["atr"], "prec": prec,
            "max_bars": sig.get("max_bars", C.MAX_BARS), "trail_atr": sig.get("trail_atr", C.TRAIL_ATR), "p": C.TP1_FRAC if partial else 1.0, "stage": 1, "bars": 0, "realized": 0.0,
            "cost_r": C.COST_PCT / (risk / e * 100), "closed": False,
            "x": sig["x"], "hour": sig["hour"], "wday": sig["wday"]}


def step_trade(tr, bar):
    """Avanza una vela cerrada con las MISMAS reglas que el backtest y la ejecución real.
    Si SL y objetivo caen en la misma vela, se asume el SL primero."""
    ev = []
    s = 1 if tr["side"] == "LONG" else -1
    end_ms = int(bar["time"]) + tr["tf_ms"]
    tr["bars"] += 1
    if tr["stage"] == 1:
        if _stop_hit(s, bar, tr["sl"]):
            close_at(tr, tr["sl"], "SL", end_ms)
            return ["SL"]
        if _target_hit(s, bar, tr["tp1"]):
            if tr["p"] >= 1.0:
                close_at(tr, tr["tp1"], "TP2", end_ms)
                return ["TP2"]
            tr["realized"] += tr["p"] * s * (tr["tp1"] - tr["entry"]) / tr["risk"]
            tr["stage"] = 2
            if C.MOVE_BE:
                tr["sl"] = tr["entry"]
            ev.append("TP1")
    else:
        if _stop_hit(s, bar, tr["sl"]):
            reason = "BE" if abs(tr["sl"] - tr["entry"]) < 1e-12 else "TRAIL"
            close_at(tr, tr["sl"], reason, end_ms)
            return ev + [reason]
        if C.TRAIL_ON:
            cand = bar["close"] - s * tr.get("trail_atr", C.TRAIL_ATR) * tr["atr"]
            tr["sl"] = max(tr["sl"], cand) if s > 0 else min(tr["sl"], cand)
        elif _target_hit(s, bar, tr["tp2"]):
            close_at(tr, tr["tp2"], "TP2", end_ms)
            return ev + ["TP2"]
    if tr["bars"] >= tr.get("max_bars", C.MAX_BARS):
        close_at(tr, float(bar["close"]), "TIME", end_ms)
        ev.append("TIME")
    return ev


class Ledger:
    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.open, self.closed = [], []
        try:
            with open(path) as f:
                d = json.load(f)
            self.open, self.closed = d.get("open", []), d.get("closed", [])
        except Exception:
            pass

    def save(self):
        with self.lock:
            self.closed = self.closed[-3000:]
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump({"open": self.open, "closed": self.closed}, f)
                os.replace(tmp, self.path)
            except Exception:
                pass

    def open_count(self): return len(self.open)
    def has_open(self, sym, mode=None):
        return any(t["sym"] == sym and (mode is None or t.get("mode") == mode) for t in self.open)

    def count_open(self, mode):
        return sum(1 for t in self.open if t.get("mode") == mode)

    def open_symbols(self): return list({t["sym"] for t in self.open})
    def add(self, tr):
        if not any(t["id"] == tr["id"] for t in self.open):
            self.open.append(tr)

    def add_closed(self, row):
        with self.lock:
            self.closed.append(row)

    def closed_since(self, since_ms):
        return [t for t in self.closed if since_ms is None or t.get("exit_ms", 0) >= since_ms]

    def update(self, sym, df):
        """Avanza las virtuales abiertas de `sym` con las velas nuevas. -> [(evento, trade)]"""
        out = []
        for tr in [t for t in self.open if t["sym"] == sym]:
            evs = []
            for ts, row in zip(df.index, df.itertuples(index=False)):
                tms = int(ts.timestamp() * 1000)
                if tms <= tr["last_ms"]:
                    continue
                evs += step_trade(tr, {"time": tms, "high": row.high, "low": row.low, "close": row.close})
                tr["last_ms"] = tms
                if tr["closed"]:
                    break
            out += [(e, tr) for e in evs]
            if tr["closed"]:
                self.open = [t for t in self.open if t["id"] != tr["id"]]
                self.closed.append({k: v for k, v in tr.items() if k != "x"})
        return out

    def expire_stale(self, now_ms):
        out = []
        for tr in [t for t in self.open if now_ms - t["last_ms"] > (C.MAX_BARS * 2 + 5) * t["tf_ms"]]:
            close_at(tr, tr["entry"], "EXPIRED", now_ms)
            tr["R"] = tr["realized"]
            self.open = [t for t in self.open if t["id"] != tr["id"]]
            out.append(("EXPIRED", tr))
        return out
