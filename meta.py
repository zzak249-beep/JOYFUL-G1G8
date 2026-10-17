"""Meta-labeling (logística en numpy), estacionalidad aprendida y 'cerebro' que aprende de cada operación."""
import json
import math
import os
import numpy as np
import config as C


def _nz(v, d=0.0):
    try:
        v = float(v)
    except Exception:
        return d
    return d if np.isnan(v) else v


def make_x(F, i, s, code, risk, price, A):
    """Vector de características en el momento de la señal (todo orientado a favor de la operación)."""
    r = F["rsi"]; hs = F["hist"]; rv = F["rvol"]
    g = int(code[1]) - (4 if s == -1 else 0)  # 1..4
    clv = F["clv"][i] if s == 1 else 1 - F["clv"][i]
    a4 = _nz(F["a4"][i], 1.0) or 1.0
    a2 = _nz(F["a2"][i], 1.0) or 1.0
    h = F["hour"][i]
    return [
        1.0 if code in ("G1", "G2", "G3", "G5", "G6", "G7") else 0.0,
        1.0 if g == 1 else 0.0, 1.0 if g == 2 else 0.0, 1.0 if g == 3 else 0.0,
        s * (_nz(r[i], 50) - 50) / 50,
        s * (_nz(r[i]) - _nz(r[i - 1])) / 10,
        s * _nz(hs[i]) / A * 10,
        s * (_nz(hs[i]) - _nz(hs[i - 1])) / A * 10,
        min(_nz(rv[i], 1), 5),
        min(max(_nz(rv[i], 1), _nz(rv[i - 1], 1)), 5),
        _nz(clv, 0.5),
        float(np.clip(s * _nz(F["dist"][i]), -5, 5)),
        float(np.clip(_nz(F["atr_ratio"][i], 1), 0, 4)),
        float(np.clip(s * _nz(F["s4"][i]) / a4, -3, 3)),
        float(np.clip(s * _nz(F["s2"][i]) / a2, -3, 3)),
        s * (_nz(F["sc15"][i], 50) - 50) / 50,
        s * (_nz(F["sc1"][i], 50) - 50) / 50,
        s * (_nz(F["sc4"][i], 50) - 50) / 50,
        math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24),
        1.0 if F["wday"][i] >= 5 else 0.0,
        float(np.clip(s * _nz(F["funding"][i]) / C.FUNDING_EXTREME, -3, 3)),
        risk / A,
        float(np.clip(C.COST_PCT / (risk / price * 100), 0, 1)),
        1.0 if F["strong"][i] else 0.0,
        1.0 if F.get("tradfi") else 0.0,
    ]


def sigmoid(z): return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d


CODES = [f"G{i}" for i in range(1, 9)]


def code_stats(samples, code, half, prior):
    """(n, media encogida, error) del R neto de un código, con olvido por número de operaciones."""
    rs = [s["r"] for s in samples if s.get("code") == code]
    n = len(rs)
    if n == 0:
        return 0, 0.0, 1.0
    r = np.array(rs, float)
    w = 0.5 ** (np.arange(n)[::-1] / half)
    ne, sm, q = w.sum(), (w * r).sum(), (w * r * r).sum()
    v = max(q / ne - (sm / ne) ** 2, 0.05)
    return n, float(sm / (ne + prior)), float(math.sqrt(v / (ne + prior)))


def dual_gate(samples, code):
    """Opera si ambos horizontes ven ventaja, o si el corto gira con fuerza y el largo no es negativo."""
    n, mS, seS = code_stats(samples, code, C.DUAL_HALF_SHORT, C.DUAL_PRIOR_SHORT)
    _, mL, _ = code_stats(samples, code, C.DUAL_HALF_LONG, C.DUAL_PRIOR_LONG)
    if n < C.DUAL_MIN_N:
        return True, (n, mS, mL)
    ok = (mS > C.DUAL_GOOD and mL > C.DUAL_GOOD) or ((mS - C.DUAL_Z * seS) > C.DUAL_GOOD and mL > -0.05)
    return ok, (n, mS, mL)


def v9_memory_ok(samples, code):
    """Puerta de memoria de V9: >= V9_MEM_MIN operaciones, corto > edge y largo > 0.6*edge (R neto, con olvido)."""
    n_all = sum(1 for s in samples if s.get("code") == code)
    _, mS, _ = code_stats(samples, code, C.V9_MEM_HALF_S, C.V9_MEM_PRIOR_S)
    _, mL, _ = code_stats(samples, code, C.V9_MEM_HALF_L, C.V9_MEM_PRIOR_L)
    if n_all < C.V9_MEM_MIN:
        return True, (n_all, mS, mL)
    return (mS > C.V9_EDGE and mL > C.V9_EDGE * 0.6), (n_all, mS, mL)


def rank_gate(samples, code):
    """Solo los RANK_TOPK mejores códigos (por límite inferior) entre los que ya tienen datos."""
    lbs = {}
    for c in CODES:
        n, m, se = code_stats(samples, c, C.RANK_HALF, C.RANK_PRIOR)
        if n >= C.DUAL_MIN_N:
            lbs[c] = m - C.RANK_Z * se
    if code not in lbs:
        return True, None
    rank = sum(1 for v in lbs.values() if v > lbs[code])
    return (rank < C.RANK_TOPK and lbs[code] > C.RANK_MIN_EDGE), rank + 1


class MetaGate:
    def __init__(self):
        self.beta = None; self.mu = None; self.sd = None; self.base = 0.5; self.n = 0

    def fit(self, samples):
        if len(samples) < C.META_MIN_N:
            return False
        X = np.array([s["x"] for s in samples], float)
        y = np.array([s["y"] for s in samples], float)
        age = np.arange(len(y))[::-1]
        w = 0.5 ** (age / C.META_HALF_LIFE) if C.META_HALF_LIFE > 0 else np.ones(len(y))
        mu = X.mean(0); sd = X.std(0) + 1e-9
        Z = np.c_[np.ones(len(X)), (X - mu) / sd]
        beta = np.zeros(Z.shape[1])
        reg = np.r_[0.0, np.ones(Z.shape[1] - 1)] * C.META_L2
        for _ in range(30):
            p = sigmoid(Z @ beta)
            g = Z.T @ (w * (p - y)) + reg * beta
            H = (Z.T * (w * p * (1 - p))) @ Z + np.diag(reg) + 1e-6 * np.eye(len(beta))
            step = np.linalg.solve(H, g)
            beta -= step
            if np.abs(step).max() < 1e-6:
                break
        self.beta, self.mu, self.sd = beta, mu, sd
        self.base = float(np.average(y, weights=w)); self.n = len(y)
        return True

    def p(self, x):
        z = (np.array(x, float) - self.mu) / self.sd
        return float(sigmoid(self.beta[0] + z @ self.beta[1:]))

    def allow(self, x, strict=False):
        if self.beta is None:
            return (not strict), None
        p = self.p(x)
        return p >= self.base + C.META_MARGIN, p

    def save(self, path):
        if self.beta is None:
            return
        with open(path, "w") as f:
            json.dump({"beta": self.beta.tolist(), "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                       "base": self.base, "n": self.n}, f)

    def load(self, path):
        try:
            with open(path) as f:
                d = json.load(f)
            self.beta = np.array(d["beta"]); self.mu = np.array(d["mu"]); self.sd = np.array(d["sd"])
            self.base = d["base"]; self.n = d["n"]
            return True
        except Exception:
            return False


def _key(wday, hour): return (1 if wday >= 5 else 0, int(hour) // 4)


def season_add(stats, s):
    k = _key(s["wday"], s["hour"])
    n, sr = stats.get(k, (0, 0.0))
    stats[k] = (n + 1, sr + s["r"])


def season_ok(stats, hour, wday):
    n, sr = stats.get(_key(wday, hour), (0, 0.0))
    if n < C.SEASON_MIN_N:
        return True, None
    m = sr / (n + C.SEASON_K)
    return m >= C.SEASON_BLOCK, m


class Brain:
    """Carga modelo + muestras, decide si una señal pasa y aprende de cada operación cerrada."""

    def __init__(self):
        self.model_path = os.path.join(C.DATA_DIR, f"{C.PREFIX}_meta_model.json")
        self.samples_path = os.path.join(C.DATA_DIR, f"{C.PREFIX}_samples.jsonl")
        self.gate = MetaGate()
        self.samples = []
        self.stats = {}
        self.new = 0
        if not self.gate.load(self.model_path):
            self.gate.load(C.ROOT_MODEL)
        src = self.samples_path if os.path.exists(self.samples_path) else C.ROOT_SAMPLES
        if os.path.exists(src):
            with open(src) as f:
                for line in f:
                    try:
                        self.samples.append(json.loads(line))
                    except Exception:
                        pass
        for s in self.samples:
            season_add(self.stats, s)

    def decay_mult(self):
        taken = [s for s in self.samples if s.get("src") != "blocked"][-C.DECAY_N:]
        if C.DECAY_ON and len(taken) >= C.DECAY_N and sum(s["r"] for s in taken) / len(taken) <= -C.DECAY_R:
            return C.DECAY_MULT, f"degradación: últimas {C.DECAY_N} = {sum(s['r'] for s in taken) / len(taken):+.2f}R/op → tamaño ×{C.DECAY_MULT:g}"
        return 1.0, ""

    def check(self, sig):
        """-> (ok, texto, multiplicador de tamaño, p)"""
        notes, ok, mult, pv = [], True, 1.0, None
        if C.SEASON_ON:
            s_ok, m = season_ok(self.stats, sig["hour"], sig["wday"])
            if m is not None:
                notes.append(f"horario {m:+.2f}R")
            if not s_ok:
                ok = False
        if C.STRATEGY == "v9" and C.V9_MEMORY_ON:
            m_ok, (mn, mS_, mL_) = v9_memory_ok(self.samples, sig["code"])
            notes.append(f"memoria V9 n={mn} corto {mS_:+.2f}R largo {mL_:+.2f}R")
            ok = ok and m_ok
        if C.DUAL_ON:
            d_ok, (dn, dS, dL) = dual_gate(self.samples, sig["code"])
            notes.append(f"dual n={dn} corto {dS:+.2f}R largo {dL:+.2f}R")
            ok = ok and d_ok
        if C.RANK_TOPK > 0:
            r_ok, rk = rank_gate(self.samples, sig["code"])
            if rk:
                notes.append(f"ranking #{rk}")
            ok = ok and r_ok
        if C.META_ON:
            a, p = self.gate.allow(sig["x"], strict=C.META_STRICT)
            if p is None:
                notes.append("meta: sin modelo")
                if C.META_STRICT:
                    ok = False
            else:
                pv = p
                notes.append(f"meta p={p:.2f} (base {self.gate.base:.2f})")
                if not a:
                    ok = False
                elif C.META_SIZING:
                    mult = float(np.clip(p / self.gate.base, 0.7, 1.3))
        dm, dtxt = self.decay_mult()
        if dm < 1.0:
            mult *= dm
            notes.append(dtxt)
        return ok, " · ".join(notes), mult, pv

    def record(self, sample):
        self.samples.append(sample)
        season_add(self.stats, sample)
        try:
            os.makedirs(C.DATA_DIR, exist_ok=True)
            with open(self.samples_path, "a") as f:
                f.write(json.dumps(sample) + "\n")
        except Exception:
            pass
        self.new += 1
        if C.META_ON and self.new >= C.META_REFIT:
            self.new = 0
            if self.gate.fit(self.samples):
                self.gate.save(self.model_path)
