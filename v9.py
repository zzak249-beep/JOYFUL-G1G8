"""Motor 'Granville V9' (Crowding + Adaptativo) portado desde el script de TradingView.
Señales: 0 cruce L · 1 rebote L · 2 extremo L · 3 cruce S · 4 rebote S · 5 extremo S  (etiquetas G1,G3,G4,G5,G7,G8).
Con V9_FAITHFUL=false se corrigen 4 fallos del script (ver README); con true se replican tal cual."""
import math
import numpy as np
import pandas as pd
import config as C
from indicators import sma, atr as atr_f
import meta
import extras

LABELS = ["G1", "G3", "G4", "G5", "G7", "G8"]


def hap_v9(close):
    """HaP de V9: 24 EMAs (3·i). El script normaliza los pesos con 85 (máx. real 139.7 -> score >100)."""
    ws = [math.sqrt(3.0 * i) for i in range(1, 25)]
    emas = [close.ewm(span=3 * i, adjust=False).mean() for i in range(1, 25)]
    ups = [(e > e.shift(1)).astype(float) for e in emas]
    rise = sum(ups) / 24.0 * 100.0
    wtot = 85.0 if C.V9_FAITHFUL else sum(ws)
    wr = sum(u * w for u, w in zip(ups, ws)) / wtot * 100.0
    return (0.40 * rise + 0.60 * wr).ewm(span=4, adjust=False).mean()


def _sh(a):
    return np.r_[np.nan, a[:-1]]


def _codes(df, ma, atr):
    c, o, h, l = (df[k].values for k in ("close", "open", "high", "low"))
    body = np.abs(c - o)
    strong = body > sma(pd.Series(body, index=df.index), 20).values * C.V9_STRENGTH
    slope = ma - _sh(ma)
    dist = (c - ma) / atr
    cp, mp = _sh(c), _sh(ma)
    buy_cross = (c > ma) & (cp <= mp) & (slope > 0) & strong & (c > o)
    buy_bounce = (l <= ma * 1.0015) & (c > ma) & (c > o) & (slope > 0) & strong & ~buy_cross
    buy_ext = (dist < -C.V9_DEV) & (c > o) & strong & (slope >= 0)
    sell_cross = (c < ma) & (cp >= mp) & (slope < 0) & strong & (c < o)
    sell_bounce = (h >= ma * 0.9985) & (c < ma) & (c < o) & (slope < 0) & strong & ~sell_cross
    sell_ext = (dist > C.V9_DEV) & (c < o) & strong & (slope <= 0)
    code = np.select([buy_cross, buy_bounce, buy_ext, sell_cross, sell_bounce, sell_ext], [0, 1, 2, 3, 4, 5], -1)
    return code, strong


def has_candidate(df):
    """Prefiltro barato (solo velas base): ¿hay señal V9 en la última vela cerrada?"""
    ma = sma(df.close, C.MA_LEN).values
    a = atr_f(df, C.ATR_LEN).values
    if len(df) < 60 or np.isnan(ma[-1]) or np.isnan(a[-1]):
        return False
    return int(_codes(df, ma, a)[0][-1]) >= 0


def prepare(F, df):
    code, strong = _codes(df, F["ma"], F["atr"])
    return {"code": code, "strong": strong, "hap": hap_v9(df.close).values,
            "move": np.abs(F["close"] - _sh(F["close"])) / F["atr"],
            "range": (F["high"] - F["low"]) / F["atr"], "sig_idx": np.flatnonzero(code >= 0)}


def signal_at(F, i):
    V = F["v9"]
    if i < 150 or V["code"][i] < 0:
        return None
    k = int(V["code"][i])
    s = 1 if k < 3 else -1
    long = s == 1
    label = LABELS[k]
    price, A = float(F["close"][i]), float(F["atr"][i])
    hr = int(F["hour"][i])
    if C.V9_USE_SESSION and ((C.V9_AVOID_ASIA and 0 <= hr < 6) or (C.V9_AVOID_LATE and hr >= 21)):
        return None
    rv = F["rvol"][i]
    if np.isnan(rv) or rv < C.V9_RVOL_MIN or V["range"][i] > C.V9_MAX_RANGE_ATR:
        return None

    # --- crowding: densidad de señales iguales entre las últimas V9_CROWD_LOOK (incluida la actual)
    idx = V["sig_idx"]
    pos = int(np.searchsorted(idx, i, side="right"))
    recent = idx[max(0, pos - C.V9_CROWD_LOOK):pos]
    density = vol_avg = speed_avg = 0.0
    if len(recent) > 2:
        same = V["code"][recent] == k
        cnt = int(same.sum())
        density = cnt / len(recent)
        if cnt > 0:
            vol_avg = float(np.nanmean(F["rvol"][recent][same]))
            speed_avg = float(np.nanmean(V["move"][recent][same]))
    crowd = max(density, 0.0)
    if vol_avg > 1.7:
        crowd = max(crowd, 0.55)
    if speed_avg > 0.9:
        crowd = max(crowd, 0.60)
    crowd_ok = (not C.V9_USE_CROWD) or crowd <= C.V9_CROWD_MAX
    if not crowd_ok:
        return None

    # --- meta score (a mano, como en el script; el TF mayor usa velas CERRADAS)
    hap = float(V["hap"][i])
    mom = hap - float(V["hap"][i - 5])
    rsi_v, htf, vw = F["rsi"][i], F["s4"][i], F["vwap"][i]
    dv = (price - vw) / A
    m = 0.0
    m += 0.16 if (hap > 56 and long) or (hap < 44 and not long) else 0.0
    m += 0.10 if (long and 46 < rsi_v < 74) or (not long and 26 < rsi_v < 54) else 0.0
    m += 0.12 if rv > 1.20 else 0.0
    m += 0.14 if (htf > 0 and long) or (htf < 0 and not long) else 0.0
    m += 0.10 if (dv < -0.30 and long) or (dv > 0.30 and not long) else 0.0
    m += 0.08 if V["strong"][i] else 0.0
    m += 0.08 if (mom > 0 and long) or (mom < 0 and not long) else 0.0
    m += 0.12 if crowd_ok else -0.30
    meta_p = 1.0 / (1.0 + math.exp(-4.5 * (m - 0.25)))
    if C.V9_USE_META and meta_p < C.V9_META_MIN:
        return None

    ok_x, xmult, xnotes = extras.apply(F, i, s, k in (0, 1, 3, 4))
    if not ok_x:
        return None

    # --- régimen adaptativo
    sb = hap >= C.V9_HAP_STRONG and mom >= 0
    sbear = hap <= 100.0 - C.V9_HAP_STRONG and mom <= 0
    neutral = C.V9_NEUTRAL_LOW < hap < C.V9_NEUTRAL_HIGH
    if C.V9_USE_REGIME:
        strong_reg = (sb or sbear) if C.V9_FAITHFUL else (sb if long else sbear)
        if strong_reg:
            rm, sm, tp2r, mb, name = 1.28, 1.60, 2.60, 22, "fuerte"
        elif neutral:
            rm, sm, tp2r, mb, name = 0.65, 1.32, 1.90, 11, "neutro"
        else:
            rm, sm, tp2r, mb, name = 1.0, 1.32, 2.20, 16, "normal"
    else:
        rm, sm, tp2r, mb, name = 1.0, 1.38, 2.20, 16, "sin régimen"
    # fiel: riskMult ensancha el stop (y por tanto REDUCE el tamaño). Corregido: el stop no cambia y riskMult escala el tamaño.
    risk = sm * A * (rm if C.V9_FAITHFUL else 1.0)
    reg_size = 1.0 if C.V9_FAITHFUL else rm
    size_mult = float(np.clip(meta_p * (1.0 - min(crowd * 1.15, 0.75)), 0.35, 1.15))

    return {
        "side": "long" if long else "short", "s": s, "code": label, "kinds": [label], "book": 4,
        "entry": price, "risk": risk, "atr": A, "sl": price - s * risk,
        "tp1": price + s * C.TP1_R * risk, "tp2": price + s * tp2r * risk, "tp2_r": tp2r,
        "tp_single": price + s * C.TP_SINGLE_R * risk, "max_bars": mb, "regime": name,
        "hour": hr, "wday": int(F["wday"][i]), "risk_mult": size_mult * reg_size * xmult,
        "trail_atr": C.V9_TRAIL_STRONG if name == "fuerte" else C.V9_TRAIL_NORMAL,
        "x": meta.make_x(F, i, s, label, risk, price, A),
        "stats": {"er": float(F["er"][i]), "adx": float(F["adx"][i]), "rvol": float(rv), "body": float(F["body_ratio"][i])},
        "notes": [f"Régimen HaP: {name} (score {hap:.0f}) · TP2 {tp2r:g}R · {mb} velas",
                  f"Meta {meta_p:.2f} · crowding {crowd:.2f} · tamaño ×{size_mult * reg_size * xmult:.2f}"] + xnotes,
        "bar_ts": int(F["idx"][i].timestamp() * 1000),
    }
