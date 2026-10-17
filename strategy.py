"""Señales Granville (G1-G8) + filtros por libro (0 Base, 1 V2, 2 V3, 3 V4).
signal_at(F, i, book) evalúa la vela i de las características de features.build()."""
import numpy as np
import config as C
import meta
import extras

TREND = {"G1", "G2", "G3", "G5", "G6", "G7"}


def _session_ok(hour):
    if hour >= 21 or hour < 7:
        return C.ALLOW_ASIA
    if hour < 13:
        return C.ALLOW_EU
    return C.ALLOW_US


def _cands(F, i):
    c, o, ma, dist = F["close"], F["open"], F["ma"], F["dist"]
    A = F["atr"][i]
    if i < 50 or np.isnan(A) or np.isnan(ma[i - 5]):
        return []
    out = []
    for s in (1, -1):
        if (s == 1 and not C.ALLOW_LONG) or (s == -1 and not C.ALLOW_SHORT):
            continue
        off = 0 if s == 1 else 4
        ext = F["low"] if s == 1 else F["high"]
        d0, d1 = s * (c[i] - ma[i]), s * (c[i - 1] - ma[i - 1])
        n, k = 0, i - 1
        while n < 40 and k > 0 and s * (c[k] - ma[k]) <= 0:
            n += 1
            k -= 1
        green = s * (c[i] - o[i]) > 0
        slp = s * (ma[i] - ma[i - 1])
        rising5 = s * (ma[i] - ma[i - 5]) > 0
        cross = d0 > 0 and d1 <= 0
        if cross and n > C.SPRING_BARS and slp > 0 and F["strong"][i] and green:
            out.append((s, f"G{1 + off}"))
        if cross and 1 <= n <= C.SPRING_BARS and rising5 and green:
            out.append((s, f"G{2 + off}"))
        touch = s * (ext[i] - ma[i])
        if (n == 0 and d0 > 0 and rising5 and slp > 0 and -0.8 * A <= touch <= 0.3 * A
                and green and np.nanmax(s * dist[i - 5:i]) >= 1.0):
            out.append((s, f"G{3 + off}"))
        if np.nanmin(s * dist[i - 2:i + 1]) <= -C.DEV_MULT and green and s * (c[i] - c[i - 1]) > 0:
            out.append((s, f"G{4 + off}"))
    return out


def signal_at(F, i, book):
    if C.STRATEGY == "v9":
        import v9
        return v9.signal_at(F, i)
    cands = _cands(F, i)
    if not cands:
        return None
    price, A = float(F["close"][i]), float(F["atr"][i])
    for s, code in cands:
        trend = code in TREND
        if book == 0 or not trend:
            risk = C.STOP_MULT * A
        else:
            if s == 1:
                slp = min(F["low"][i], F["ma"][i]) - C.STRUCT_BUF * A
            else:
                slp = max(F["high"][i], F["ma"][i]) + C.STRUCT_BUF * A
            risk = s * (price - slp)
            if risk > C.MAX_RISK_ATR * A:
                continue
            risk = max(risk, C.MIN_RISK_ATR * A)

        notes = []
        if book >= 1:
            if not _session_ok(int(F["hour"][i])):
                continue
            s4, a4 = F["s4"][i], F["a4"][i]
            if np.isnan(s4) or np.isnan(a4):
                continue
            aligned = s4 > 0 if s == 1 else s4 < 0
            flat = abs(s4) < C.HTF_FLAT * a4
            if trend and not aligned:
                continue
            if not trend and not (aligned or flat):
                continue
            if aligned:
                notes.append(f"HTF {C.HTF_TF}: {'alcista' if s == 1 else 'bajista'} ✓")
            rv = F["rvol"][i]
            if np.isnan(rv) or rv < C.RVOL_MIN:
                continue
            if not trend and C.USE_VWAP_EXT:
                vw = F["vwap"][i]
                if (s == 1 and price >= vw) or (s == -1 and price <= vw):
                    continue
            if trend and C.USE_ER and F["er"][i] < C.ER_MIN:
                continue
            if trend and C.USE_BODY and F["body_ratio"][i] < C.BODY_MIN:
                continue
            if not trend and C.USE_CLIMAX and rv < C.CLIMAX_MULT:
                continue
            if C.COST_MULT > 0 and (risk / price * 100) < C.COST_MULT * C.COST_PCT:
                continue
            if C.FUNDING_ON:
                f = F["funding"][i]
                if not np.isnan(f) and s * f > C.FUNDING_EXTREME:
                    continue

        if book >= 2:
            r, hist = F["rsi"], F["hist"]
            if np.isnan(r[i]) or np.isnan(r[i - 1]) or np.isnan(hist[i]) or np.isnan(hist[i - 1]):
                continue
            if trend:
                if s == 1 and not (C.RSI_TREND_MIN <= r[i] <= C.RSI_CHASE_MAX):
                    continue
                if s == -1 and not (100 - C.RSI_CHASE_MAX <= r[i] <= 100 - C.RSI_TREND_MIN):
                    continue
            else:
                w = r[i - C.RSI_LOOK:i + 1]
                if s == 1 and np.nanmin(w) > C.RSI_OS:
                    continue
                if s == -1 and np.nanmax(w) < 100 - C.RSI_OS:
                    continue
            if s * (r[i] - r[i - 1]) <= 0 or s * (hist[i] - hist[i - 1]) <= 0:
                continue
            if np.nanmax(F["rvol"][i - C.RVOL2_BARS + 1:i + 1]) < C.RVOL2_MIN:
                continue
            clv = F["clv"][i]
            if (s == 1 and clv < C.CLV_MIN) or (s == -1 and clv > 1 - C.CLV_MIN):
                continue
            if trend:
                s2 = F["s2"][i]
                if np.isnan(s2) or s * s2 <= 0:
                    continue
            if C.COST_PCT / (risk / price * 100) > C.MAX_COST_R:
                continue

        if book >= 3:
            v, sc1, sc4 = F["sc15"][i], F["sc1"][i], F["sc4"][i]
            if np.isnan(v) or np.isnan(sc1) or np.isnan(sc4):
                if C.HAP_BLOCK_IF_MISSING:
                    continue
            else:
                x = v if s == 1 else 100 - v
                m1 = sc1 if s == 1 else 100 - sc1
                m2 = sc4 if s == 1 else 100 - sc4
                if trend:
                    if not (C.HAP_MIN <= x <= C.HAP_MAX):
                        continue
                    if s * (v - F["sc15"][i - C.HAP_MOM_LEN]) <= 0:
                        continue
                    if m1 < C.HAP_MTF1_MIN or m2 < C.HAP_MTF2_MIN:
                        continue
                elif m2 < C.HAP_EXT_FLOOR:
                    continue

        ok_x, xmult, xnotes = extras.apply(F, i, s, trend)
        if not ok_x:
            continue
        notes.extend(xnotes)
        mult = 1.0
        if C.VOL_SCALE_ON:
            ar = F["atr_ratio"][i]
            if not np.isnan(ar) and ar > 0:
                mult = float(np.clip(1.0 / ar, C.VOL_SCALE_LO, C.VOL_SCALE_HI))
        mult *= xmult
        tp2r, maxb, rmult, regime = C.TP2_R, C.MAX_BARS, 1.0, ""
        if C.REGIME_EXITS and not np.isnan(F["sc15"][i]) and i >= 5:
            v = F["sc15"][i]
            x = v if s == 1 else 100 - v
            if not trend:
                regime, tp2r, rmult = "reversión", C.RX_TP2_MR, C.RX_RISK_MR
            elif x >= C.RX_STRONG and s * (v - F["sc15"][i - 5]) > 0:
                regime, tp2r, maxb = "fuerte", C.RX_TP2_STRONG, C.MAX_BARS + C.RX_BARS_STRONG
            elif 40 < v < 60:
                regime, tp2r, maxb, rmult = "neutro", C.RX_TP2_NEUTRAL, max(4, C.MAX_BARS + C.RX_BARS_NEUTRAL), C.RX_RISK_NEUTRAL
        if regime:
            notes.append(f"Régimen HaP: {regime} (TP2 {tp2r:g}R · {maxb} velas)")
        return {
            "side": "long" if s == 1 else "short", "s": s, "code": code, "book": book,
            "entry": price, "risk": risk, "atr": A, "sl": price - s * risk,
            "tp1": price + s * C.TP1_R * risk, "tp2": price + s * tp2r * risk, "tp2_r": tp2r, "max_bars": maxb, "regime": regime,
            "tp_single": price + s * C.TP_SINGLE_R * risk,
            "hour": int(F["hour"][i]), "wday": int(F["wday"][i]), "risk_mult": mult * rmult,
            "kinds": [code], "notes": notes,
            "stats": {"er": float(F["er"][i]), "adx": float(F["adx"][i]),
                      "rvol": None if np.isnan(F["rvol"][i]) else float(F["rvol"][i]),
                      "body": float(F["body_ratio"][i])},
            "x": meta.make_x(F, i, s, code, risk, price, A),
            "bar_ts": int(F["idx"][i].timestamp() * 1000),
        }
    return None


def has_candidate(F):
    return bool(_cands(F, len(F["close"]) - 1))
