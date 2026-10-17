import math
import numpy as np
import pandas as pd


def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rma(s, n): return s.ewm(alpha=1.0 / n, adjust=False).mean()


def atr(df, n):
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def rsi(c, n):
    d = c.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma((-d).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 - 100 / (1 + up / dn)


def macd_hist(c, f, s, sig):
    m = ema(c, f) - ema(c, s)
    return m - ema(m, sig)


def vwap_daily(df):
    tp = (df.high + df.low + df.close) / 3
    day = df.index.normalize()
    pv = (tp * df.volume).groupby(day).cumsum()
    v = df.volume.groupby(day).cumsum()
    return pv / v


def hap_score(src, first, step, smooth, w_slope, w_period, w_align):
    """Reconstrucción del HaP EMA Strength: 30 EMAs, score 0-100.
    Mezcla: % EMAs subiendo, % ponderado por raíz del periodo, % de pares vecinos alineados."""
    periods = [first] + [step * i for i in range(1, 30)]
    emas = [src.ewm(span=p, adjust=False).mean() for p in periods]
    ups = [(e > e.shift(1)).astype(float) for e in emas]
    rise = sum(ups) / len(ups)
    w = [math.sqrt(p) for p in periods]
    wper = sum(u * wi for u, wi in zip(ups, w)) / sum(w)
    align = sum((emas[i] >= emas[i + 1]).astype(float) for i in range(len(emas) - 1)) / (len(emas) - 1)
    tot = w_slope + w_period + w_align
    raw = 100.0 * (w_slope * rise + w_period * wper + w_align * align) / tot
    return raw.ewm(span=smooth, adjust=False).mean()


def efficiency_ratio(c, n):
    den = c.diff().abs().rolling(n).sum()
    return ((c - c.shift(n)).abs() / den.replace(0, np.nan)).fillna(0.0)


def adx(df, n):
    h, l, c = df.high, df.low, df.close
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = rma(tr, n)
    pdi, mdi = 100 * rma(pdm, n) / a, 100 * rma(mdm, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx.fillna(0.0), n)
