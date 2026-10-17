"""Estadísticas en R (formato de tu escáner)."""
import html
import time
import numpy as np


def summarize(rows):
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r.get("exit_ms", 0))
    rs = np.array([float(r["R"]) for r in rows])
    n = len(rs)
    win = rs > 0
    gp, gl = float(rs[win].sum()), float(-rs[~win].sum())
    sd = float(rs.std(ddof=1)) if n > 1 else 0.0
    eq = np.r_[0.0, np.cumsum(rs)]
    return {"n": n, "wr": float(win.mean()), "avg_r": float(rs.mean()), "total_r": float(rs.sum()),
            "pf": gp / gl if gl > 0 else float("inf"),
            "t": float(rs.mean() / (sd / np.sqrt(n))) if sd > 1e-6 else 0.0,
            "maxdd": float((np.maximum.accumulate(eq) - eq).max())}


def verdict(sm):
    if not sm:
        return "sin datos"
    if sm["n"] < 30:
        return "muestra insuficiente (menos de 30 ops)"
    if sm["t"] >= 2.0:
        return "ventaja probable"
    if sm["t"] <= -2.0:
        return "ventaja NEGATIVA"
    return "sin evidencia de ventaja"


def fmt_pf(x): return "∞" if x == float("inf") else f"{x:.2f}"


def stats_message(ledger, open_count, days=None, title="📊 Estadísticas"):
    since = None if days is None else int((time.time() - days * 86400) * 1000)
    rows_all = ledger.closed_since(since)
    span = "total" if days is None else f"últimos {days:g} días"
    lines = [f"<b>{title}</b> ({span})", f"Operaciones abiertas ahora: {open_count}"]
    if not rows_all:
        lines.append("Aún no hay operaciones cerradas.")
        return "\n".join(lines)
    for mode, label in (("virtual", "virtuales (sin dinero)"), ("real", "reales en BingX")):
        rows = [r for r in rows_all if r.get("mode") == mode]
        sm = summarize(rows)
        if not sm:
            continue
        lines += ["", f"<b>{label}</b>",
                  f"Cerradas: {sm['n']} · win rate {sm['wr'] * 100:.0f}%",
                  f"Expectancia: <b>{sm['avg_r']:+.2f}R</b>/op · total {sm['total_r']:+.1f}R · PF {fmt_pf(sm['pf'])}",
                  f"Racha de pérdida máx.: {sm['maxdd']:.1f}R · t-stat {sm['t']:.2f}",
                  f"Veredicto: {verdict(sm)}"]
        by = {}
        for r in rows:
            by.setdefault("+".join(r["kinds"]), []).append(r)
        if len(by) > 1:
            for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
                s2 = summarize(v)
                lines.append(f"{k}: n={s2['n']} · {s2['avg_r']:+.2f}R/op · WR {s2['wr'] * 100:.0f}%")
    lines.append("\n<i>R neto de costes. Las virtuales usan tus reglas de salida sin ejecución real.</i>")
    return "\n".join(lines)
