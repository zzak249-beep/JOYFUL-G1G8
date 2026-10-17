"""Mensajes de Telegram con el MISMO formato que tu escáner Granville (señal, hitos, estadísticas, abiertas)."""
import html
from datetime import datetime, timezone
import config as C

KIND_LABEL = {
    "G1": "Cruce alcista", "G2": "Recuperación tras perder la media", "G3": "Rebote en la media",
    "G4": "Sobreventa extrema", "G5": "Cruce bajista", "G6": "Rechazo tras ruptura falsa",
    "G7": "Rechazo en la media", "G8": "Sobrecompra extrema",
}
TV_INTERVAL = {"15m": "15", "1h": "60", "4h": "240"}
EVENT_TEXT = {
    "SL": ("🛑", "Stop loss"), "TP2": ("🏆", "TP2 alcanzado"), "BE": ("⚪", "Cierre en break-even"),
    "TRAIL": ("✅", "Cierre por trailing"), "TIME": ("⏱", "Cierre por tiempo"),
    "SIGNAL": ("🔁", "Cierre por señal contraria"), "EXPIRED": ("❔", "Sin datos, operación descartada"),
}


def fmt_price(x, prec): return f"{x:.{prec}f}"


def signal_message(sig, sym_id, prec, account, live, now_ms, info=""):
    long = sig["side"] == "long"
    icon = "🟢" if long else "🔴"
    kinds = " + ".join(f"{k} ({KIND_LABEL.get(k, k)})" for k in sig["kinds"])
    base = sym_id.split("-")[0]
    price, risk = sig["entry"], sig["risk"]
    mult = sig.get("risk_mult", 1.0)
    risk_cash = account * C.RISK_PCT / 100 * mult
    qty = risk_cash / risk if risk > 0 else 0.0
    notional, capped = qty * price, ""
    if notional > account * C.MAX_LEV:
        notional = account * C.MAX_LEV
        qty = notional / price
        capped = f" (limitado a {C.MAX_LEV:g}x)"
    sl_pct = risk / price * 100
    end_ms = sig["bar_ts"] + C.TF_SEC * 1000
    close_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    age = max(0, int((now_ms - end_ms) / 60_000))
    st = sig.get("stats", {})
    parts = []
    if "er" in st:
        parts += [f"ER {st['er']:.2f}", f"ADX {st['adx']:.0f}"]
    if st.get("rvol") is not None:
        parts.append(f"RVOL {st['rvol']:.2f}")
    if "body" in st:
        parts.append(f"cuerpo {st['body']:.2f}")
    tv = (f"https://www.tradingview.com/chart/?symbol=BINGX:{sym_id.replace('-', '')}.P"
          f"&interval={TV_INTERVAL.get(C.TF, '15')}")
    if C.PARTIAL_MODE:
        tp_line = (f"🎯 TP1: <code>{fmt_price(sig['tp1'], prec)}</code> ({C.TP1_R:g}R) · "
                   f"TP2: <code>{fmt_price(sig['tp2'], prec)}</code> ({sig.get('tp2_r', C.TP2_R):g}R)")
        rest = f"con trailing {sig.get('trail_atr', C.TRAIL_ATR):g}×ATR" if C.TRAIL_ON else "hasta TP2"
        manage = (f"📋 Gestión: cierra {C.TP1_FRAC * 100:g}% en TP1 y mueve el SL a la entrada · el resto {rest}"
                  f" · sal si pasan {sig.get('max_bars', C.MAX_BARS)} velas")
    else:
        tp_line = f"🎯 TP: <code>{fmt_price(sig['tp_single'], prec)}</code> ({C.TP_SINGLE_R:g}R)"
        manage = f"📋 Gestión: cierre completo en el TP · sal si pasan {sig.get('max_bars', C.MAX_BARS)} velas"
    head_size = "Tamaño real" if live else "Tamaño sugerido"
    lines = [
        f"{icon} <b>{sig['side'].upper()}</b> · <b>{html.escape(sym_id)}</b> · {C.TF}",
        f"<b>{kinds}</b>", "",
        f"Precio ref. (cierre vela): <code>{fmt_price(price, prec)}</code>",
        f"🛑 SL: <code>{fmt_price(sig['sl'], prec)}</code> ({risk / sig['atr']:.1f} ATR ≈ {sl_pct:.2f}%)",
        tp_line, "",
        f"{head_size} (riesgo {C.RISK_PCT * mult:.2f}% de {account:g} USDT = {risk_cash:.2f} USDT):",
        f"<code>{qty:.6g} {html.escape(base)}</code> ≈ {notional:.2f} USDT nominal{capped}", "",
        " · ".join(parts),
    ]
    lines += list(sig.get("notes", []))
    if info:
        lines.append(f"🧠 {info}")
    lines += ["", manage, f"Vela cerrada {close_dt:%H:%M} UTC (hace {age} min)", f'<a href="{tv}">Abrir gráfico</a>', ""]
    lines.append("<i>Orden real enviada a BingX con tu dinero.</i>" if live else
                 "<i>Señal orientativa, no es consejo financiero. Revisa el gráfico antes de entrar.</i>")
    return "\n".join(lines)


def event_message(tr, ev, R=None, exit_px=None):
    """tr: dict con side, symbol, tf, kinds, entry, tp2, prec, bars."""
    head = f"<b>{tr['side']}</b> · <b>{html.escape(tr['symbol'])}</b> · {tr['tf']} · {'+'.join(tr['kinds'])}"
    if ev == "TP1":
        rest = f"con trailing {tr.get('trail_atr', C.TRAIL_ATR):g}×ATR" if C.TRAIL_ON else f"hacia TP2 <code>{fmt_price(tr['tp2'], tr['prec'])}</code>"
        return (f"🎯 TP1 alcanzado · {head}\n"
                f"Cierra {C.TP1_FRAC * 100:g}% y mueve el SL a la entrada <code>{fmt_price(tr['entry'], tr['prec'])}</code>.\n"
                f"El resto sigue {rest}.")
    icon, label = EVENT_TEXT.get(ev, ("ℹ️", ev))
    R = tr.get("R", 0.0) if R is None else R
    px = tr.get("exit_px", tr["entry"]) if exit_px is None else exit_px
    return (f"{icon} {label} · {head}\n"
            f"Salida ≈ <code>{fmt_price(px, tr['prec'])}</code> · resultado <b>{R:+.2f}R</b> "
            f"(neto de costes) · {tr['bars']} velas")


def open_message(items):
    if not items:
        return "No hay operaciones abiertas."
    lines = [f"<b>Operaciones abiertas ({len(items)})</b>"]
    for t in items:
        stage = "tras TP1 (SL en entrada)" if t["stage"] == 2 else "esperando TP1"
        tag = "real" if t["mode"] == "real" else "virtual"
        lines.append(f"{'🟢' if t['side'] == 'LONG' else '🔴'} {html.escape(t['symbol'])} {t['tf']} {'+'.join(t['kinds'])} · "
                     f"ent {fmt_price(t['entry'], t['prec'])} · SL {fmt_price(t['sl'], t['prec'])} · {stage} · "
                     f"{t['bars']} velas · {tag}")
    return "\n".join(lines)
