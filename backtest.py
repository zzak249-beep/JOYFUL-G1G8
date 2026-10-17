"""Backtester con walk-forward honesto.
Ejemplos:
  python backtest.py --compare                      # 15m vs 1h x libros 0-3 (sin meta)
  python backtest.py --tf 15m --book 1 --days 365   # un caso con walk-forward (meta/estacionalidad fuera de muestra)
  python backtest.py --book 1 --train-meta          # guarda meta_model.json + samples.jsonl para el bot
  python backtest.py --selftest                     # prueba con datos sintéticos (no significativa)
Salidas y entradas: entrada al cierre de la vela de señal; si SL y TP caen en la misma vela, se asume SL primero.
El coste (COST_PCT) se resta en R. No incluye funding pagado ni límites de posiciones simultáneas."""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import config as C
import features
import meta
import strategy
import universe


# ---------------- datos ----------------
def _ex():
    import ccxt
    return ccxt.bingx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def load_ohlcv(sym, tf, days, refresh=False):
    os.makedirs("data", exist_ok=True)
    path = f"data/{sym.replace('/', '_').replace(':', '_')}_{tf}_{days}d.csv"
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    ex = _ex()
    dur = C.tf_sec(tf) * 1000
    since = int(time.time() * 1000) - days * 86400000
    rows = []
    while since < time.time() * 1000:
        b = ex.fetch_ohlcv(sym, tf, since=since, limit=1000)
        if not b:
            break
        rows += b
        nxt = b[-1][0] + dur
        if nxt <= since:
            break
        since = nxt
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df.index = pd.to_datetime(df.ts, unit="ms", utc=True)
    df = df.drop(columns="ts").astype(float).sort_index().iloc[:-1]
    df.to_csv(path)
    return df


def load_funding(sym, days):
    try:
        ex = _ex()
        since = int(time.time() * 1000) - days * 86400000
        rows = []
        while True:
            b = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
            if not b:
                break
            rows += [(x["timestamp"], x["fundingRate"]) for x in b]
            nxt = b[-1]["timestamp"] + 1
            if nxt <= since or len(b) < 2:
                break
            since = nxt
        s = pd.Series({pd.to_datetime(t, unit="ms", utc=True): r for t, r in rows}).sort_index()
        return s if len(s) else None
    except Exception as e:
        print("  (funding no disponible:", e, ")")
        return None


def synthetic(n=40000, seed=1):
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0004, n // 400 + 1), 400)[:n]
    r = drift + rng.normal(0, 0.002, n)
    close = 100 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0008, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0008, n)))
    vol = rng.lognormal(5, 0.5, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": vol}, index=idx)


# ---------------- simulación ----------------
def simulate(F, i, sig):
    s, e, risk, A = sig["s"], sig["entry"], sig["risk"], sig["atr"]
    n = len(F["close"])
    stop = e - s * risk
    tp1 = e + s * C.TP1_R * risk
    partial = C.PARTIAL_MODE
    target = e + s * (sig.get("tp2_r", C.TP2_R) if partial else C.TP_SINGLE_R) * risk
    rem, real, tp1_done = 1.0, 0.0, not partial
    last = min(n - 1, i + sig.get("max_bars", C.MAX_BARS))
    for j in range(i + 1, last + 1):
        hi, lo, cl = F["high"][j], F["low"][j], F["close"][j]
        if (lo <= stop) if s == 1 else (hi >= stop):
            return j, real + rem * s * (stop - e) / risk, "SL" if (rem == 1.0) else "BE/TRAIL"
        if not tp1_done:
            if (hi >= tp1) if s == 1 else (lo <= tp1):
                real += C.TP1_FRAC * C.TP1_R
                rem = 1 - C.TP1_FRAC
                tp1_done = True
                if C.MOVE_BE:
                    stop = e
        elif C.TRAIL_ON and partial:
            cand = cl - s * sig.get("trail_atr", C.TRAIL_ATR) * A
            stop = max(stop, cand) if s == 1 else min(stop, cand)
        elif (hi >= target) if s == 1 else (lo <= target):
            return j, real + rem * s * (target - e) / risk, "TP"
        if C.SMART_TIME_ON and (j - i) >= max(6, sig.get("max_bars", C.MAX_BARS) // 2) and not np.isnan(F["sc15"][j]) and s * (F["sc15"][j] - 50.0) < 0:
            return j, real + rem * s * (cl - e) / risk, "TIME_HAP"
    cl = F["close"][last]
    return last, real + rem * s * (cl - e) / risk, "TIME"


def gen_trades(sym, F, book, cls="crypto"):
    n = len(F["close"])
    trades, i = [], 250
    dur = pd.Timedelta(seconds=C.TF_SEC)
    while i < n - 1:
        if not universe.market_open(cls, F["idx"][i] + dur):
            i += 1
            continue
        sig = strategy.signal_at(F, i, book)
        if not sig:
            i += 1
            continue
        j, R, kind = simulate(F, i, sig)
        cost = C.COST_PCT / (sig["risk"] / sig["entry"] * 100)
        Rn = R - cost
        trades.append({"sym": sym, "code": sig["code"], "side": sig["side"], "kind": kind,
                       "entry_ts": F["idx"][i] + dur, "exit_ts": F["idx"][j] + dur,
                       "R": Rn, "r": Rn, "m": sig.get("risk_mult", 1.0), "y": int(Rn > 0), "x": sig["x"],
                       "hour": sig["hour"], "wday": sig["wday"]})
        i = j + 1
    return trades


# ---------------- evaluación ----------------
def metrics(trades):
    if not trades:
        return None
    t = sorted(trades, key=lambda z: z["exit_ts"])
    R = np.array([z["R"] for z in t])
    wins = int((R > 0).sum())
    gl = -R[R < 0].sum()
    cum = np.cumsum(R)
    w = np.array([z.get("m", 1.0) for z in t])
    curve = np.cumprod(1 + C.RISK_PCT / 100 * w * R)
    eq = curve[-1] - 1
    ddp = float(((np.maximum.accumulate(np.r_[1.0, curve]) - np.r_[1.0, curve]) / np.maximum.accumulate(np.r_[1.0, curve])).max() * 100)
    return {"n": len(R), "win": wins / len(R), "wlow": meta.wilson(wins, len(R)), "avgR": R.mean(),
            "pf": (R[R > 0].sum() / gl) if gl > 0 else float("inf"),
            "sumR": R.sum(), "ddR": float((np.maximum.accumulate(cum) - cum).max()), "ddp": ddp, "ret": eq * 100}


def row(name, m):
    if not m:
        return f"{name:<28} sin operaciones"
    return (f"{name:<28} n={m['n']:<5} win={m['win']*100:5.1f}% (IC95≥{m['wlow']*100:4.1f}%) "
            f"R/op={m['avgR']:+.3f} PF={m['pf']:.2f} ΣR={m['sumR']:+.1f} DD={m['ddR']:.1f}R ({m['ddp']:.0f}%) ret@{C.RISK_PCT}%={m['ret']:+.0f}%")


def walk_forward(trades, use_meta, use_season, refit=25, use_dual=False, use_rank=False, use_v9mem=False):
    tr = sorted(trades, key=lambda z: z["entry_ts"])
    by_exit = sorted(trades, key=lambda z: z["exit_ts"])
    gate, known, stats, p, last_fit, taken = meta.MetaGate(), [], {}, 0, 0, []
    for t in tr:
        while p < len(by_exit) and by_exit[p]["exit_ts"] <= t["entry_ts"]:
            known.append(by_exit[p]); meta.season_add(stats, by_exit[p]); p += 1
        if use_meta and len(known) >= C.META_MIN_N and len(known) - last_fit >= refit:
            gate.fit(known); last_fit = len(known)
        ok = True
        if use_meta:
            ok = ok and gate.beta is not None and gate.allow(t["x"])[0]
        if use_season:
            ok = ok and meta.season_ok(stats, t["hour"], t["wday"])[0]
        if use_dual:
            ok = ok and meta.dual_gate(known, t["code"])[0]
        if use_rank:
            ok = ok and meta.rank_gate(known, t["code"])[0]
        if use_v9mem:
            ok = ok and meta.v9_memory_ok(known, t["code"])[0]
        if ok:
            taken.append(t)
    return taken


def quarters(trades):
    d = {}
    for t in trades:
        d.setdefault(f"{t['entry_ts'].year}Q{(t['entry_ts'].month-1)//3+1}", []).append(t)
    for k in sorted(d):
        print("   ", row(k, metrics(d[k])))


# ---------------- ejecución ----------------
def prepare(sym, tf, days, funding_on, synth=False, cls="crypto"):
    C.set_tf(tf)
    if synth:
        base = synthetic()
        if tf == "1h":
            base = features.resample(base, "1h")
    else:
        base = load_ohlcv(sym, tf, days)
    htf = {t: (base if t == tf else features.resample(base, t)) for t in features.needed_tfs()}
    fund = load_funding(sym, days) if funding_on and not synth else None
    F = features.build(base, htf, fund)
    F["tradfi"] = universe.is_tradfi(cls)
    return F


CLS = {}


def run_case(symbols, tf, book, days, funding_on, synth=False):
    all_t = []
    for sym in symbols:
        cls = CLS.get(sym, "crypto")
        try:
            F = prepare(sym, tf, days, funding_on, synth, cls)
        except Exception as e:
            print(f"  (omitido {sym}: {e})")
            continue
        all_t += gen_trades(sym, F, book, cls)
    return all_t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(C.SYMBOLS))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--tf", default=C.TF)
    ap.add_argument("--book", type=int, default=C.BOOK)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--compare-exits", action="store_true")
    ap.add_argument("--train-meta", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=30)
    ap.add_argument("--v9", action="store_true")
    ap.add_argument("--compare-ideas", action="store_true")
    ap.add_argument("--vt", action="store_true")
    ap.add_argument("--regime4", action="store_true")
    ap.add_argument("--mtf-strict", action="store_true")
    ap.add_argument("--delta", action="store_true")
    ap.add_argument("--fund-pct", action="store_true")
    ap.add_argument("--smart-time", action="store_true")
    ap.add_argument("--regime-exits", action="store_true")
    ap.add_argument("--funding", action="store_true")
    ap.add_argument("--trail", action="store_true")
    ap.add_argument("--vol-scale", action="store_true")
    a = ap.parse_args()
    if a.v9:
        C.set_strategy("v9")
    for flag, attr in ((a.vt, "VT_ON"), (a.regime4, "REGIME4_ON"), (a.mtf_strict, "MTF_STRICT"), (a.delta, "DELTA_ON"),
                       (a.fund_pct, "FUND_PCT_ON"), (a.smart_time, "SMART_TIME_ON")):
        if flag:
            setattr(C, attr, True)
    C.REGIME_EXITS = a.regime_exits or C.REGIME_EXITS
    C.TRAIL_ON = a.trail or C.TRAIL_ON
    C.VOL_SCALE_ON = a.vol_scale or C.VOL_SCALE_ON
    C.FUNDING_ON = a.funding or C.FUNDING_ON
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
    if any(s.upper() == "ALL" for s in syms) and not a.selftest:
        u = universe.build(_ex())[:a.max_symbols]
        syms = [s for s, _, _ in u]
        CLS.update({s: c for s, c, _ in u})
        print(f"Universo: {len(syms)} símbolos (los {a.max_symbols} más líquidos)")
    if a.selftest:
        syms = ["SYNTH"]

    if a.compare_ideas:
        keys = ("VT_ON", "REGIME4_ON", "MTF_STRICT", "DELTA_ON", "FUND_PCT_ON", "SMART_TIME_ON", "TRAIL_ON")
        base = {k: getattr(C, k) for k in keys}
        variants = [("base", {}), ("+ volatility targeting", {"VT_ON": True}), ("+ régimen 4 estados", {"REGIME4_ON": True}),
                    ("+ MTF estricto (HaP TF mayor)", {"MTF_STRICT": True}), ("+ delta/absorción", {"DELTA_ON": True}),
                    ("+ trailing ATR", {"TRAIL_ON": True}), ("+ salida inteligente (HaP)", {"SMART_TIME_ON": True})]
        if a.funding:
            variants.append(("+ funding percentil", {"FUND_PCT_ON": True}))
        print(f"Comparación de ideas · {C.STRATEGY} · TF {a.tf} · libro {a.book} · coste {C.COST_PCT}%\n")
        for name, fl in variants:
            for k, v in base.items():
                setattr(C, k, v)
            for k, v in fl.items():
                setattr(C, k, v)
            print(row(name, metrics(run_case(syms, a.tf, a.book, a.days, a.funding, a.selftest))))
        return

    if a.compare_exits:
        print(f"Comparación de salidas · TF {a.tf} · libro {a.book} · coste {C.COST_PCT}%\n")
        base = run_case(syms, a.tf, a.book, a.days, a.funding, a.selftest)  # solo para precargar datos
        for name, trail, atr_m, partial in (("TP2 fijo (actual)", False, 2.0, True), ("Trailing 1.5 ATR", True, 1.5, True),
                                            ("Trailing 2 ATR", True, 2.0, True), ("Trailing 3 ATR", True, 3.0, True),
                                            ("Salida simple 1.5R", False, 2.0, False)):
            C.TRAIL_ON, C.TRAIL_ATR, C.PARTIAL_MODE = trail, atr_m, partial
            print(row(name, metrics(run_case(syms, a.tf, a.book, a.days, a.funding, a.selftest))))
        return

    if a.compare:
        print(f"Comparación (sin meta, coste {C.COST_PCT}%, {a.days} días)\n")
        for tf in ("15m", "1h"):
            for book in range(4):
                tr = run_case(syms, tf, book, a.days, a.funding, a.selftest)
                print(row(f"{tf} libro {book}", metrics(tr)))
        return

    tr = run_case(syms, a.tf, a.book, a.days, a.funding, a.selftest)
    print(f"\nTF {a.tf} · libro {a.book} · {len(tr)} señales simuladas\n")
    print(row("Sin puertas", metrics(tr)))
    print(row("+ estacionalidad (WF)", metrics(walk_forward(tr, False, True))))
    print(row("+ meta-labeling (WF)", metrics(walk_forward(tr, True, False))))
    if C.STRATEGY == "v9":
        print(row("+ memoria V9 (WF)", metrics(walk_forward(tr, False, False, use_v9mem=True))))
    C.RANK_TOPK = C.RANK_TOPK or 3
    print(row("+ memoria dual (WF)", metrics(walk_forward(tr, False, False, use_dual=True))))
    print(row(f"+ ranking top-{C.RANK_TOPK} (WF)", metrics(walk_forward(tr, False, False, use_rank=True))))
    both = walk_forward(tr, True, True)
    print(row("+ ambas (WF)", metrics(both)))
    if both:
        print("\nPor trimestre (ambas puertas, fuera de muestra):")
        quarters(both)
    print("\nLas filas '(WF)' solo usan operaciones ya cerradas antes de cada señal. Las primeras "
          f"{C.META_MIN_N} operaciones no se operan (el modelo aún no existe).")

    if a.train_meta and not a.selftest:
        g = meta.MetaGate()
        ts = sorted(tr, key=lambda z: z["exit_ts"])
        if g.fit(ts):
            g.save(C.ROOT_MODEL)
            with open(C.ROOT_SAMPLES, "w") as f:
                for z in ts:
                    f.write(json.dumps({"x": z["x"], "y": z["y"], "r": z["r"], "code": z["code"], "hour": z["hour"],
                                        "wday": z["wday"]}) + "\n")
            print(f"\nGuardado meta_model.json y samples.jsonl ({len(ts)} muestras, tasa base {g.base:.2f}).")
        else:
            print("\nMuestras insuficientes para entrenar.")


if __name__ == "__main__":
    main()
