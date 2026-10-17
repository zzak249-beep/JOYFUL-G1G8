import os


def _s(k, d=""): return os.getenv(k, d).strip()
def _f(k, d): return float(os.getenv(k, str(d)))
def _i(k, d): return int(float(os.getenv(k, str(d))))
def _b(k, d): return os.getenv(k, str(d)).strip().lower() in ("1", "true", "yes", "si", "sí", "y")


# ---- credenciales / modo
BINGX_KEY = _s("BINGX_API_KEY")
BINGX_SECRET = _s("BINGX_API_SECRET")
TG_TOKEN = _s("TELEGRAM_TOKEN")
TG_CHAT = _s("TELEGRAM_CHAT_ID")
DRY_RUN = _b("DRY_RUN", True)
DEMO = _b("BINGX_DEMO", False)
POSITION_MODE = _s("POSITION_MODE", "hedge").lower()
SYMBOLS = [x.strip() for x in _s("SYMBOLS", "ALL").split(",") if x.strip()]
SCAN_ALL = any(x.upper() == "ALL" for x in SYMBOLS)   # ALL = analiza todo el universo filtrado de BingX
CLASSES = [x.strip().lower() for x in _s("ASSET_CLASSES", "crypto,stock").split(",") if x.strip()]
MIN_VOL_USDT = _f("MIN_VOL_USDT", 3_000_000)   # volumen 24h mínimo
MAX_SYMBOLS = _i("MAX_SYMBOLS", 120)
MAX_SPREAD_PCT = _f("MAX_SPREAD_PCT", 0.06)    # spread máximo (% del precio)
EXCLUDE = [x.strip().upper() for x in _s("EXCLUDE", "").split(",") if x.strip()]
STOCK_RTH_ONLY = _b("STOCK_RTH_ONLY", True)    # acciones: solo sesión regular NYSE (9:30-16:00 ET)
UNIVERSE_REFRESH = _i("UNIVERSE_REFRESH", 3600)
for _x in SYMBOLS:                      # SYMBOLS=TOP:30 -> los 30 más líquidos
    if _x.upper().startswith("TOP:"):
        SCAN_ALL = True
        MAX_SYMBOLS = int(_x.split(":")[1])
BOOK = _i("BOOK", 1)
RATE_LIMIT_MS = _i("RATE_LIMIT_MS", 250)          # BingX devolvía 100410 (límite de peticiones) con 100 ms
DAILY_LOSS_PCT = _f("DAILY_LOSS_PCT", 3.0)         # freno: si el día pierde ≥ x% del capital, no abre más hasta el día UTC siguiente
MAX_POSITIONS = _i("MAX_POSITIONS", 3)
DATA_DIR = _s("DATA_DIR", "") or os.path.dirname(_s("STATE_PATH", "")) or "."
STATE_PATH = os.path.join(DATA_DIR, "granville_state.json")   # nombre propio: no pisa archivos de otros bots

# ---- timeframe (15m o 1h). Los TF superiores se derivan solos.
_DUR = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_MAP = {"15m": ("4h", "1h", "1h", "4h"), "1h": ("1d", "4h", "4h", "1d")}


def tf_sec(tf): return _DUR[tf]


def set_tf(tf):
    global TF, TF_SEC, HTF_TF, HTF2_TF, HAP_TF1, HAP_TF2
    TF = tf
    TF_SEC = _DUR[tf]
    HTF_TF, HTF2_TF, HAP_TF1, HAP_TF2 = _MAP[tf]


set_tf(_s("TF", "15m"))

# ---- riesgo / ejecución
RISK_PCT = _f("RISK_PCT", 1.0)
MAX_LEV = _f("MAX_LEV", 10)
LEVERAGE = _i("LEVERAGE", 10)
PARTIAL_MODE = _b("PARTIAL_MODE", True)
TP_SINGLE_R = _f("TP_SINGLE_R", 1.5)
ALLOW_LONG = _b("ALLOW_LONG", True)
ALLOW_SHORT = _b("ALLOW_SHORT", True)

# ---- base (igual que el Pine)
MA_LEN = 50
MA_TYPE = "SMA"
ATR_LEN = 14
STRENGTH_MULT = 1.2
DEV_MULT = 2.5

# ---- salidas
STOP_MULT = 1.5
TP1_R = 1.0
TP2_R = 2.0
TP1_FRAC = 0.5
MOVE_BE = True
MAX_BARS = _i("MAX_BARS", 18)
COST_PCT = _f("COST_PCT", 0.12)

# ---- stop estructural
STRUCT_BUF = 0.3
MIN_RISK_ATR = 0.7
MAX_RISK_ATR = 3.0
SPRING_BARS = 3

# ---- filtros V2
HTF_FLAT = 0.10
RVOL_LEN = 20
RVOL_MIN = 1.2
USE_VWAP_EXT = True
ALLOW_ASIA = True
ALLOW_EU = True
ALLOW_US = True

# ---- V3
RSI_LEN = 14
RSI_TREND_MIN = 50.0
RSI_CHASE_MAX = 75.0
RSI_OS = 30.0
RSI_LOOK = 3
MACD_F, MACD_S, MACD_SIG = 12, 26, 9
RVOL2_MIN = 1.4
RVOL2_BARS = 2
CLV_MIN = 0.65
MAX_COST_R = 0.25

# ---- V4 (HaP reconstruido)
HAP_FIRST = 3
HAP_STEP = 3
HAP_SMOOTH = 2
HAP_W_SLOPE = 0.35
HAP_W_PERIOD = 0.35
HAP_W_ALIGN = 0.30
HAP_WARM_F = 1.5
HAP_MIN = 55.0
HAP_MAX = 90.0
HAP_MOM_LEN = 5
HAP_MTF1_MIN = 50.0
HAP_MTF2_MIN = 50.0
HAP_EXT_FLOOR = 40.0
HAP_BLOCK_IF_MISSING = True

# ================= MEJORAS NUEVAS (todas desactivables) =================
# 3) Meta-labeling: regresión logística que aprende cuándo la señal gana
META_ON = _b("META_ON", True)
META_MIN_N = _i("META_MIN_N", 80)          # muestras mínimas para fiarse del modelo
META_MARGIN = _f("META_MARGIN", 0.02)      # exige p >= tasa base + margen
META_HALF_LIFE = _f("META_HALF_LIFE", 400) # olvido (en operaciones); 0 = sin olvido
META_L2 = _f("META_L2", 5.0)
META_REFIT = _i("META_REFIT", 10)          # reentrenar cada N operaciones nuevas
META_STRICT = _b("META_STRICT", False)     # True: sin modelo entrenado no opera
META_SIZING = _b("META_SIZING", False)     # tamaño según p (0.7x–1.3x)
# 6) Estacionalidad aprendida (12 cubos: finde/laborable x tramo de 4h)
SEASON_ON = _b("SEASON_ON", True)
SEASON_MIN_N = _i("SEASON_MIN_N", 20)
SEASON_K = _f("SEASON_K", 10.0)
SEASON_BLOCK = _f("SEASON_BLOCK", -0.10)   # bloquea cubos con R medio encogido < x
# 4) Funding (OI no se incluye: no hay histórico para validarlo)
FUNDING_ON = _b("FUNDING_ON", False)
FUNDING_EXTREME = _f("FUNDING_EXTREME", 0.0003)  # 0,03% por 8h
# 5) Trailing ATR tras TP1 (sustituye al TP2) y escalado por volatilidad
TRAIL_ON = _b("TRAIL_ON", False)
TRAIL_ATR = _f("TRAIL_ATR", 2.0)
VOL_SCALE_ON = _b("VOL_SCALE_ON", False)
VOL_SCALE_LO = 0.5
VOL_SCALE_HI = 1.25

# ---- avisos tipo "escáner" (formato Telegram, seguimiento virtual, comandos)
ACCOUNT_USDT = _f("ACCOUNT_USDT", 1000)          # solo para el tamaño sugerido en modo señales
MAX_OPEN_PAPER = _i("MAX_OPEN_TRADES", 8)        # tope de operaciones virtuales abiertas (DRY_RUN)
MAX_SIGNALS_PER_SCAN = _i("MAX_SIGNALS_PER_SCAN", 15)
COMMANDS_ON = _b("TELEGRAM_COMMANDS", True)
SUMMARY_HOUR = _i("SUMMARY_HOUR_UTC", 8)         # -1 = sin resumen diario

# ---- filtros extra tomados de tu escáner (apagados por defecto: mide con backtest antes)
USE_ER = _b("USE_ER", False)                     # régimen: eficiencia de Kaufman en códigos de tendencia
ER_LEN = 20
ER_MIN = _f("ER_MIN", 0.25)
USE_BODY = _b("USE_BODY", False)                 # cuerpo/rango mínimo en códigos de tendencia
BODY_MIN = _f("BODY_MIN", 0.55)
USE_CLIMAX = _b("USE_CLIMAX", False)             # extremos (g4/g8) solo con RVOL de clímax
CLIMAX_MULT = _f("CLIMAX_MULT", 1.8)
COST_MULT = _f("COST_MULT", 0.0)                 # stop% >= x * coste%  (tu escáner usa 5; 0 = apagado)

# ---- límites de concentración y tipo de orden del TP
MAX_SAME_DIR = _i("MAX_SAME_DIR", 2)             # máx. posiciones abiertas en la misma dirección (long o short)
TP_ORDER = _s("TP_ORDER", "trigger").lower()     # trigger = disparador (visible en TP/SL, comisión taker) | limit = límite (comisión maker, posible no-ejecución)

# ---- ideas del otro análisis, implementadas con interruptor (todas APAGADAS salvo la sombra: mídelas con backtest)
DUAL_ON = _b("DUAL_ON", False)                    # memoria por código en 2 horizontes (corto/largo)
DUAL_HALF_SHORT, DUAL_HALF_LONG = 18.0, 90.0
DUAL_PRIOR_SHORT, DUAL_PRIOR_LONG = 8.0, 30.0
DUAL_MIN_N = _i("DUAL_MIN_N", 10)
DUAL_GOOD = _f("DUAL_GOOD", 0.05)
DUAL_Z = 0.5
RANK_TOPK = _i("RANK_TOPK", 0)                    # 0 = off; k = solo los k mejores códigos por límite inferior
RANK_HALF, RANK_PRIOR, RANK_Z, RANK_MIN_EDGE = 40.0, 12.0, 0.5, 0.0
REGIME_EXITS = _b("REGIME_EXITS", False)          # TP2/barras/tamaño según régimen HaP (el stop NO cambia)
RX_STRONG = 70.0
RX_TP2_STRONG, RX_TP2_NEUTRAL, RX_TP2_MR = 2.8, 1.6, 1.5
RX_BARS_STRONG, RX_BARS_NEUTRAL = 8, -6
RX_RISK_NEUTRAL, RX_RISK_MR = 0.7, 0.6
SHADOW_ON = _b("SHADOW_ON", True)                 # en real, sigue cada operación también como virtual (mide la brecha)
SLIP_MAX_R = _f("SLIP_MAX_R", 0.15)               # slippage medio (R) que activa el recorte de tamaño al 50%
KILL_MIN_N = _i("KILL_MIN_N", 15)                 # kill-switch: operaciones reales y virtuales mínimas
KILL_GAP_R = _f("KILL_GAP_R", 0.5)                # pausa si virtual - real > x R/op
MAX_SPREAD_R = _f("MAX_SPREAD_R", 0.15)           # spread máx. como fracción del riesgo (stop)

# ================= ESTRATEGIA V9 (Crowding + Adaptativo) =================
V9_FAITHFUL = _b("V9_FAITHFUL", False)   # true = replica los fallos del script (HaP/85, riskMult en el stop, régimen sin dirección)
V9_STRENGTH, V9_DEV = 1.15, 2.35
V9_USE_REGIME = _b("V9_USE_REGIME", True)
V9_HAP_STRONG, V9_NEUTRAL_LOW, V9_NEUTRAL_HIGH = 66.0, 38.0, 62.0
V9_USE_SESSION = _b("V9_USE_SESSION", True)
V9_AVOID_ASIA = _b("V9_AVOID_ASIA", True)        # evita 00:00-06:00 UTC
V9_AVOID_LATE = _b("V9_AVOID_LATE", False)       # evita 21:00-23:59 UTC
V9_RVOL_MIN, V9_MAX_RANGE_ATR = 1.10, 2.6
V9_USE_CROWD = _b("V9_USE_CROWD", True)
V9_CROWD_LOOK, V9_CROWD_MAX = 10, 0.48
V9_USE_META = _b("V9_USE_META", True)
V9_META_MIN = _f("V9_META_MIN", 0.50)
V9_MEMORY_ON = _b("V9_MEMORY_ON", True)
V9_MEM_HALF_S, V9_MEM_HALF_L = 14.0, 85.0
V9_MEM_PRIOR_S, V9_MEM_PRIOR_L = 9.0, 28.0
V9_MEM_MIN, V9_EDGE = 7, 0.03
LEARN_BLOCKED = _b("LEARN_BLOCKED", True)        # sigue como virtual (en silencio) las señales bloqueadas por las puertas: evita códigos "muertos"
MAX_LEARN = _i("MAX_LEARN", 80)


def set_strategy(name):
    """granville = motor G1-G8 reconstruido · v9 = script Granville V9. Cada uno usa sus propios ficheros."""
    global STRATEGY, PREFIX, TP1_FRAC, META_ON, SEASON_ON, STATE_PATH, ROOT_MODEL, ROOT_SAMPLES, BOT_NAME
    STRATEGY = name
    v9 = name == "v9"
    PREFIX = "v9" if v9 else "granville"
    TP1_FRAC = 0.45 if v9 else 0.5
    META_ON = _b("META_ON", not v9)
    SEASON_ON = _b("SEASON_ON", not v9)
    STATE_PATH = os.path.join(DATA_DIR, f"{PREFIX}_state.json")
    BOT_NAME = _s("BOT_NAME", "V9" if v9 else "G1-G8")
    ROOT_MODEL = "meta_model_v9.json" if v9 else "meta_model.json"
    ROOT_SAMPLES = "samples_v9.jsonl" if v9 else "samples.jsonl"


set_strategy(_s("STRATEGY", "granville").lower())

# ================= IDEAS NUEVAS (todas APAGADAS: mídelas con `backtest.py --compare-ideas`) =================
FUND_PCT_ON = _b("FUND_PCT_ON", False)           # 1) no operar a favor del lado saturado según el percentil del funding (30 días)
FUND_PCT_HI = _f("FUND_PCT_HI", 0.90)
FUND_DAYS = 30
OI_ON = _b("OI_ON", False)                       # 1) + Open Interest subiendo (SOLO EN VIVO: BingX no da histórico de OI)
OI_RISE = _f("OI_RISE", 0.05)
OI_WINDOW_H = 4
DELTA_ON = _b("DELTA_ON", False)                 # 2) delta aproximado + absorción en contra
DELTA_MIN = _f("DELTA_MIN", 0.0)
ABS_RVOL, ABS_RANGE = 1.5, 0.7
VT_ON = _b("VT_ON", False)                       # 3) volatility targeting relativo (vol corta / vol larga del propio activo)
VT_SHORT, VT_LONG = 84, 240                      # en velas del TF mayor (4h: 14 y 40 días)
VT_LO, VT_HI = 0.5, 1.25
MTF_STRICT = _b("MTF_STRICT", False)             # 4) HaP del TF mayor a favor en códigos de tendencia
MTF_HAP_MIN = _f("MTF_HAP_MIN", 50.0)
SMART_TIME_ON = _b("SMART_TIME_ON", False)       # 5) salida anticipada si el HaP se gira en contra (SOLO BACKTEST hasta demostrarlo)
V9_TRAIL_STRONG, V9_TRAIL_NORMAL = 3.0, 2.0      # 5) trailing más ancho en régimen fuerte (solo con TRAIL_ON)
REGIME4_ON = _b("REGIME4_ON", False)             # 6) tendencia fuerte / débil / rango / caos
R4_ADX_STRONG, R4_ADX_WEAK = 25.0, 18.0
R4_CHAOS_ATR, R4_CHAOS_BBW = 2.0, 0.95
R4_STRONG_HAP = 60.0
R4_SIZE_WEAK, R4_RANGE_EXT_SIZE = 0.6, 0.8
DECAY_ON = _b("DECAY_ON", False)                 # 7) kill-switch por degradación: si las últimas N operaciones rinden mal, tamaño x0.5
DECAY_N, DECAY_R, DECAY_MULT = 12, 0.3, 0.5
BOT_TAG = _b("BOT_TAG", True)                    # antepone [NOMBRE] a cada mensaje de Telegram (varios bots en el mismo chat)
