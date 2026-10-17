"""Universo de BingX: cripto + acciones (y opcionalmente índices/FX/materias primas) filtrado por liquidez y spread.
Ejecuta `python universe.py` para ver qué detecta (la clasificación es heurística: revísala)."""
import re
import config as C

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = None

_PREFIX = {"NCSK": "stock", "NCSI": "index", "NCFX": "fx", "NCCO": "commodity"}
STOCKS = set("""AAPL MSFT AMZN GOOGL GOOG META NVDA TSLA INTC AMD NFLX COIN MSTR HOOD PLTR BABA ORCL AVGO CRM
 UBER SHOP PYPL DIS BA JPM V MA WMT KO PEP NKE ADBE QCOM MU TSM ASML SNOW SMCI ARM GME AMC RDDT CRCL SPY QQQ""".split())
INDEXES = {"SP500", "NASDAQ100", "US30", "DJI", "NAS100", "SPX", "NDX", "DAX", "DE40", "UK100", "JP225", "US500", "US100"}
COMMOD = {"XAUUSD", "XAGUSD", "WTI", "BRENT", "NATGAS", "USOIL", "UKOIL", "XTIUSD", "XBRUSD", "NGAS", "COPPER", "GOLD", "SILVER"}
_CCY = {"EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNH", "MXN"}


def _norm(base):
    b = base.upper()
    for p in _PREFIX:
        if b.startswith(p):
            b = b[len(p):]
    return re.sub(r"(2USD|USD)$", "", b) if b not in COMMOD else b


def classify(m):
    ident = (m.get("id") or "").upper()
    base = (m.get("base") or "").upper()
    for p, cl in _PREFIX.items():
        if ident.startswith(p) or base.startswith(p):
            return cl
    b = _norm(base)
    if b in STOCKS or (b.endswith("X") and b[:-1] in STOCKS):
        return "stock"
    if base in INDEXES or b in INDEXES:
        return "index"
    if base in COMMOD:
        return "commodity"
    if len(base) == 6 and base[:3] in _CCY and base[3:] in _CCY:
        return "fx"
    return "crypto"


def is_tradfi(cls):
    return cls != "crypto"


def market_open(cls, ts):
    """Acciones: solo sesión regular de NYSE (lun-vie 9:30-16:00 ET). No contempla festivos."""
    if cls != "stock" or not C.STOCK_RTH_ONLY or NY is None:
        return True
    t = ts.tz_convert(NY)
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= mins < 16 * 60


def build(ex, quiet=True):
    """-> lista de (symbol, clase, volumen24h) ordenada por volumen."""
    ex.load_markets()
    tick = ex.fetch_tickers()
    rows = []
    for sym, m in ex.markets.items():
        if not (m.get("swap") and m.get("linear") and m.get("quote") == "USDT") or m.get("active") is False:
            continue
        cls = classify(m)
        if cls not in C.CLASSES or (m.get("base") or "").upper() in C.EXCLUDE:
            continue
        t = tick.get(sym) or {}
        vol = float(t.get("quoteVolume") or 0)
        if vol < C.MIN_VOL_USDT:
            continue
        bid, ask = t.get("bid"), t.get("ask")
        if bid and ask and (ask - bid) / ((ask + bid) / 2) * 100 > C.MAX_SPREAD_PCT:
            continue
        rows.append((sym, cls, vol))
    rows.sort(key=lambda r: -r[2])
    return rows[:C.MAX_SYMBOLS]


if __name__ == "__main__":
    import ccxt
    ex = ccxt.bingx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    u = build(ex)
    from collections import Counter
    print(len(u), "símbolos:", dict(Counter(c for _, c, _ in u)))
    for s, c, v in u[:60]:
        print(f"{c:<9}{s:<28}{v/1e6:8.1f}M")
