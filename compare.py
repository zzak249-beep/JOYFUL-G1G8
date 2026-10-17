"""Compara los resultados de dos bots (p. ej. G1-G8 vs V9) a partir de sus historiales (/exportar en Telegram).
  python compare.py granville_ledger.json v9_ledger.json --names G1-G8 V9 --mode virtual
  --mode virtual | real | all    (all = virtual + real)
Por defecto solo compara el periodo en que AMBOS estuvieron activos (--no-common para desactivarlo)."""
import argparse
import json
import math
import time

import numpy as np

from meta import wilson
from stats import summarize, verdict, fmt_pf


def load(path, modes):
    with open(path) as f:
        d = json.load(f)
    rows = d.get("closed", []) if isinstance(d, dict) else []
    return [r for r in rows if r.get("mode") in modes and "R" in r and r.get("exit_ms")]


def pct(x): return f"{x * 100:.0f}%"


def describe(rows, risk):
    sm = summarize(rows)
    if not sm:
        return None
    rs = np.array([float(r["R"]) for r in sorted(rows, key=lambda r: r["exit_ms"])])
    curve = np.cumprod(1 + risk / 100 * rs)
    peak = np.maximum.accumulate(np.r_[1.0, curve])
    dd = float(((peak - np.r_[1.0, curve]) / peak).max() * 100)
    wins = int((rs > 0).sum())
    sm.update(wlow=wilson(wins, len(rs)), ret=(curve[-1] - 1) * 100, ddp=dd,
              best=float(rs.max()), worst=float(rs.min()))
    return sm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--names", nargs=2, default=["A", "B"])
    ap.add_argument("--mode", default="virtual", choices=["virtual", "real", "all"])
    ap.add_argument("--risk", type=float, default=1.0, help="% de riesgo por operación para la equity aproximada")
    ap.add_argument("--no-common", action="store_true")
    a = ap.parse_args()
    modes = {"virtual": {"virtual"}, "real": {"real"}, "all": {"virtual", "real"}}[a.mode]
    ra, rb = load(a.a, modes), load(a.b, modes)
    if not ra or not rb:
        print("Alguno de los dos historiales no tiene operaciones cerradas de ese modo todavía.")
        return
    if not a.no_common:
        lo = max(min(r["exit_ms"] for r in ra), min(r["exit_ms"] for r in rb))
        hi = min(max(r["exit_ms"] for r in ra), max(r["exit_ms"] for r in rb))
        ra = [r for r in ra if lo <= r["exit_ms"] <= hi]
        rb = [r for r in rb if lo <= r["exit_ms"] <= hi]
        f = lambda ms: time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        print(f"Periodo común: {f(lo)} → {f(hi)} · modo {a.mode} · riesgo {a.risk}%\n")
    da, db = describe(ra, a.risk), describe(rb, a.risk)
    if not da or not db:
        print("No hay operaciones en el periodo común.")
        return
    na, nb = a.names
    rows = [
        ("Operaciones", f"{da['n']}", f"{db['n']}"),
        ("Win rate", pct(da["wr"]), pct(db["wr"])),
        ("  límite inferior IC95", pct(da["wlow"]), pct(db["wlow"])),
        ("R medio por operación", f"{da['avg_r']:+.3f}", f"{db['avg_r']:+.3f}"),
        ("R total", f"{da['total_r']:+.1f}", f"{db['total_r']:+.1f}"),
        ("Profit factor", fmt_pf(da["pf"]), fmt_pf(db["pf"])),
        ("t-stat", f"{da['t']:.2f}", f"{db['t']:.2f}"),
        ("Mejor / peor operación (R)", f"{da['best']:+.1f} / {da['worst']:+.1f}", f"{db['best']:+.1f} / {db['worst']:+.1f}"),
        (f"Retorno aprox. @{a.risk}%", f"{da['ret']:+.1f}%", f"{db['ret']:+.1f}%"),
        ("Drawdown máx. aprox.", f"{da['ddp']:.1f}%", f"{db['ddp']:.1f}%"),
        ("Veredicto", verdict(da), verdict(db)),
    ]
    w0 = max(len(r[0]) for r in rows) + 2
    print(f"{'':<{w0}}{na:<34}{nb:<34}")
    for r in rows:
        print(f"{r[0]:<{w0}}{r[1]:<34}{r[2]:<34}")
    # diferencia de R/op con intervalo aproximado (Welch)
    xa = np.array([r["R"] for r in ra], float)
    xb = np.array([r["R"] for r in rb], float)
    se = math.sqrt(xa.var(ddof=1) / len(xa) + xb.var(ddof=1) / len(xb)) if len(xa) > 1 and len(xb) > 1 else float("nan")
    diff = xa.mean() - xb.mean()
    print(f"\nDiferencia de R/op ({na} - {nb}): {diff:+.3f} ± {1.96 * se:.3f} (IC95 aprox.)")
    if not (abs(diff) > 1.96 * se):
        print("→ La diferencia NO es estadísticamente distinguible del azar con estos datos.")
    # por código
    print("\nPor código de señal:")
    for nm, rr in ((na, ra), (nb, rb)):
        by = {}
        for r in rr:
            by.setdefault("+".join(r.get("kinds", ["?"])), []).append(r)
        print(f"  {nm}: " + " · ".join(f"{k} n={len(v)} {summarize(v)['avg_r']:+.2f}R" for k, v in sorted(by.items())))
    print("\nNota: con menos de ~30 operaciones por bot la comparación es ruido. Las virtuales no incluyen slippage ni fills reales.")


if __name__ == "__main__":
    main()
