"""Ideas 'nuevas' como puertas/multiplicadores OPCIONALES (todas apagadas por defecto: mídelas con backtest.py --compare-ideas).
apply(F, i, s, trend) -> (ok, multiplicador de tamaño, notas)"""
import numpy as np
import config as C


def _nan(x):
    return x is None or (isinstance(x, float) and np.isnan(x))


def regime4(F, i, s):
    """Tendencia fuerte / débil / rango / caos (ADX, HaP, percentil de ATR y de ancho de Bollinger, MA200)."""
    adx, ar, bw, m200 = F["adx"][i], F["atr_ratio"][i], F["bbw_pct"][i], F["ma200"][i]
    if any(_nan(float(v)) for v in (adx, ar, bw, m200)):
        return "n/d", None
    if ar >= C.R4_CHAOS_ATR or bw >= C.R4_CHAOS_BBW:
        return "caos", "caos"
    sc = F["sc15"][i]
    x = 50.0 if _nan(float(sc)) else (sc if s == 1 else 100.0 - sc)
    if adx >= C.R4_ADX_STRONG and x >= C.R4_STRONG_HAP and s * (F["close"][i] - m200) > 0:
        return "tendencia fuerte", "fuerte"
    if adx >= C.R4_ADX_WEAK:
        return "tendencia débil", "debil"
    return "rango", "rango"


def apply(F, i, s, trend):
    mult, notes = 1.0, []

    # 4) Funding (percentil de 30 días) + Open Interest: no entrar a favor del lado saturado
    if C.FUND_PCT_ON:
        fp, f = float(F["fund_pct"][i]), float(F["funding"][i])
        if not (_nan(fp) or _nan(f)):
            if (s == 1 and fp >= C.FUND_PCT_HI and f > 0) or (s == -1 and fp <= 1 - C.FUND_PCT_HI and f < 0):
                return False, 1.0, []
            notes.append(f"funding p{fp * 100:.0f}")
    if C.OI_ON:
        oc, fp = float(F["oi_chg"][i]), float(F["fund_pct"][i])
        if not (_nan(oc) or _nan(fp)):
            if oc > C.OI_RISE and ((s == 1 and fp >= 0.80) or (s == -1 and fp <= 0.20)):
                return False, 1.0, []
            notes.append(f"OI {oc * 100:+.1f}%")

    # 2) Delta aproximado (posición del cierre x volumen) y absorción en contra
    if C.DELTA_ON:
        dn = s * float(F["delta_n"][i])
        against = False
        for k in (0, 1):
            clv = F["clv"][i - k] if s == 1 else 1.0 - F["clv"][i - k]
            if F["abs_flag"][i - k] and clv <= 0.4:
                against = True
        if against or (not _nan(dn) and dn < C.DELTA_MIN):
            return False, 1.0, []

    # 4) Confluencia estricta con el TF mayor: HaP del TF mayor a favor (solo códigos de tendencia)
    if C.MTF_STRICT and trend:
        sc4 = float(F["sc4"][i])
        x = sc4 if s == 1 else 100.0 - sc4
        if _nan(sc4) or x < C.MTF_HAP_MIN:
            return False, 1.0, []

    # 6) Clasificador de régimen en 4 estados
    if C.REGIME4_ON:
        name, st = regime4(F, i, s)
        if st == "caos":
            return False, 1.0, []
        if st:
            if st == "fuerte":
                mult *= 1.0 if trend else 0.5
            elif st == "debil":
                mult *= C.R4_SIZE_WEAK
            elif st == "rango":
                if trend:
                    return False, 1.0, []
                mult *= C.R4_RANGE_EXT_SIZE
            notes.append(f"Régimen 4: {name}")

    # 3) Volatility targeting relativo: vol realizada corta / vol propia de largo plazo (TF mayor)
    if C.VT_ON:
        rv = float(F["rv_ratio"][i])
        if not _nan(rv) and rv > 0:
            vt = float(np.clip(1.0 / rv, C.VT_LO, C.VT_HI))
            mult *= vt
            notes.append(f"vol-target ×{vt:.2f}")
    return True, mult, notes
