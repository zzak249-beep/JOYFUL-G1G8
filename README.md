# Granville Bot — BingX + Telegram + Railway

Bot en Python basado en la estrategia Pine "Granville V5". Opera futuros perpetuos USDT en BingX (15m),
avisa por Telegram y se despliega en Railway.

> ⚠️ **Operar con dinero real conlleva riesgo de pérdida total. Nada de aquí garantiza beneficios.**
> Orden obligatorio: `DRY_RUN=true` (días) → `BINGX_DEMO=true` → real con `RISK_PCT=0.1`.

## Qué hace
- Cada cierre de vela 15m evalúa G1–G8 (Granville) con el libro elegido (`BOOK`: 0 Base, 1 V2, 2 V3, 3 V4).
- Entrada a mercado, **SL en el exchange**, TP1 (1R, 50%) y TP2 (2R, 50%); tras TP1 el SL pasa a BE.
- Cierre por tiempo a las 18 velas. Tamaño por riesgo (`RISK_PCT`) y tope de apalancamiento.
- Si falla la colocación del SL tras entrar, cierra la posición por seguridad.

## 0. ANTES de operar: backtest y entrenamiento (en tu PC, necesita internet)
```bash
pip install -r requirements.txt
python backtest.py --compare                       # 15m vs 1h, libros 0-3, con coste real
python backtest.py --tf 1h --book 1 --days 540     # un caso: sin puertas / +estacionalidad / +meta / +ambas (walk-forward)
python backtest.py --book 1 --funding --trail      # prueba funding y trailing
python backtest.py --book 1 --train-meta           # crea meta_model.json + samples.jsonl
```
- Las filas `(WF)` son **fuera de muestra**: cada decisión usa solo operaciones ya cerradas.
- Sube `meta_model.json` y `samples.jsonl` al repo (o al volumen `/data`): el bot los carga y **sigue aprendiendo** de cada operación real cerrada.
- Activa solo lo que mejore el resultado fuera de muestra en TODOS los trimestres, no solo en el total.
- `python backtest.py --selftest` prueba que todo funciona con datos sintéticos (resultados sin significado).

## Avisos de Telegram (mismo formato que tu escáner Granville)
- Señal con tipo (G1…G8), precio ref., SL en ATR y %, TP1/TP2 en R, tamaño, ER/ADX/RVOL/cuerpo, HTF, gestión, hora de vela y enlace al gráfico.
- Hitos: 🎯 TP1 · 🛑 SL · 🏆 TP2 · ⚪ BE · ✅ trailing · ⏱ tiempo, con el resultado en R neto.
- En `DRY_RUN` cada señal se **sigue como operación virtual** (sin dinero) y alimenta el meta-modelo: así aprende y mides el winrate real antes de arriesgar nada.
- Comandos: `/stats` `/semana` `/abiertas` `/estado` + resumen diario. Usa un bot de Telegram propio (si otro proceso lee los comandos del mismo token habrá conflicto 409).
- No repite señales tras un redeploy (`granville_seen.json`).

## Pasar a dinero real (resumen)
1) `python check_live.py` (comprueba claves, saldo, modo de posición y tamaño operable; no abre nada) · 2) `DRY_RUN=false` + claves de la
subcuenta · 3) revisar en BingX el SL/TP de la primera operación. Cierre de emergencia: `/pausa`, `/cerrartodo` + `/confirmarcierre`.

## Dos bots independientes (G1-G8 y V9)
Dos servicios, cada uno con su volumen, su bot de Telegram y (en real) su subcuenta de BingX. `BOT_NAME` etiqueta los mensajes.
`/exportar` envía el historial y `python compare.py A_ledger.json B_ledger.json --names G1-G8 V9` los compara.
Guía: `docs/DOS_BOTS_INDEPENDIENTES.md`. Plantillas: `railway_variables_G1G8_*.txt` y `railway_variables_V9_*.txt`.

## Estrategia V9 (Crowding + Adaptativo) — `STRATEGY=v9`
Portada del script "Granville V9". Mismo bot (BingX, Telegram, universo, TP/SL, estadísticas), otro motor de señales:
cruce / rebote / extremo (6 códigos), puerta de crowding, meta-score, régimen HaP adaptativo (TP2, velas, tamaño),
filtro de sesión (evita 00-06 UTC), liquidez (RVOL y rango/ATR) y memoria dual por código.
Usa ficheros propios (`v9_*.json`), así que no se mezcla con el motor G1-G8.
- Plantillas: `railway_variables_V9_SENALES.txt` (solo avisos + seguimiento virtual) y `railway_variables_V9_REAL.txt`.
- Backtest: `python backtest.py --v9 --days 365 --tf 15m` (fila "memoria V9 (WF)" fuera de muestra) y `--train-meta`.
- `V9_FAITHFUL=false` (por defecto) corrige 4 fallos del script: (1) HaP normalizaba los pesos con 85 (el máximo real es 139,7:
  el score podía pasar de 100); (2) el filtro del TF mayor usaba datos del futuro y una pendiente mal calculada: ahora usa velas
  cerradas del TF mayor; (3) `riskMult` ensanchaba el stop y por tanto REDUCÍA el tamaño: ahora el stop no cambia y escala el tamaño;
  (4) el régimen "fuerte" se aplicaba también a operaciones en contra: ahora debe ir a favor de la dirección.
  Además la memoria aprende en silencio de las señales bloqueadas (`LEARN_BLOCKED`) para que ningún código quede "muerto".
  `V9_FAITHFUL=true` replica el script tal cual (para comparar).

## Ideas nuevas investigadas (todas apagadas) — ver `docs/IDEAS_INVESTIGACION.md`
`FUND_PCT_ON` · `OI_ON` (solo en vivo) · `DELTA_ON` · `VT_ON` · `MTF_STRICT` · `REGIME4_ON` · `DECAY_ON` · `SMART_TIME_ON` (solo backtest).
Compáralas con `python backtest.py --compare-ideas` (añade `--v9` y `--funding` si quieres).

## Scripts de TradingView (carpeta `pine/`) — versiones corregidas
Mismas señales G1-G8, filtros y salidas que el bot de Python, para poder contrastar backtests. Cada código tiene un libro
virtual propio (aprende sin mirar al futuro). Prueba con comisión 0,06% y slippage 2-3 ticks. **No se han podido compilar
aquí**: si TradingView marca un error, copia el mensaje exacto y lo corrijo.
- `granville_v6_1_regimen_memoria_dual.pine`: régimen HaP modula TP2/barras/tamaño (no el stop) + memoria corta/larga.
- `granville_v6_1_meta_labeling_online.pine`: logística online entrenada con el resultado de los libros virtuales.
- `granville_v6_1_ranking_liquidez.pine`: solo los top-K códigos por límite inferior + filtro de coste en R.

## Ideas incorporadas al bot de Python (todas con interruptor, apagadas hasta medirlas)
`DUAL_ON` memoria por código en 2 horizontes · `RANK_TOPK` solo los mejores códigos · `REGIME_EXITS` salidas por régimen HaP ·
`SHADOW_ON` seguimiento virtual en paralelo al real + kill-switch si lo real rinde peor que lo virtual (`KILL_*`) ·
recorte de tamaño por slippage (`SLIP_MAX_R`) · spread máximo como fracción del stop (`MAX_SPREAD_R`) · `/pausa` `/reanudar`.
Mídelas: `python backtest.py --book 1` (filas dual/ranking) y `--regime-exits`.
No implementado a propósito: selección online de filtros por importancia (con decenas de operaciones es ruido),
imbalance del order book y "probabilidad de fill" (no hay histórico para validarlos).

## Universo: todas las monedas y acciones de BingX
`SYMBOLS=ALL` escanea cada cierre de vela todos los perpetuos USDT que pasen `MIN_VOL_USDT` y `MAX_SPREAD_PCT` (máx. `MAX_SYMBOLS`), refrescando la lista cada hora.
- Escaneo barato: baja 120 velas por símbolo y solo descarga el resto si hay candidato a señal.
- Si varias señales coinciden, abre primero las de mayor probabilidad del meta-modelo, hasta `MAX_POSITIONS`.
- Clasificación crypto/stock/index/fx/commodity **heurística** (nombres de BingX no verificados): ejecuta `python universe.py` y revisa la lista antes de operar.
- Acciones: solo sesión NYSE (`STOCK_RTH_ONLY`), sin festivos. Fuera de horario o sin velas nuevas, se salta.
- Backtest del universo: `python backtest.py --symbols ALL --max-symbols 30 --book 1 --train-meta` (tarda por la descarga; queda en caché).
- Más símbolos = más señales correlacionadas (cripto se mueve junta): mantén `MAX_POSITIONS` bajo.

## Mejoras incluidas (todas con interruptor)
| Mejora | Variable | Qué hace |
|---|---|---|
| Meta-labeling | `META_ON` | Logística (numpy) con ~25 características; solo opera si p ≥ tasa base + margen |
| Estacionalidad | `SEASON_ON` | Bloquea tramos horarios (12 cubos) con R medio negativo aprendido |
| Funding | `FUNDING_ON` | No entra a favor del lado saturado (funding extremo) |
| Trailing ATR | `TRAIL_ON` | Tras TP1 sustituye TP2 por trailing |
| Tamaño por volatilidad | `VOL_SCALE_ON` | Reduce riesgo si el ATR está alto |
| Timeframe | `TF` | 15m o 1h |
Open Interest no se incluye: no hay histórico para validarlo.

## 1. Telegram
1. @BotFather → `/newbot` → copia el token → `TELEGRAM_TOKEN`.
2. Escríbele un mensaje al bot. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia `chat.id` → `TELEGRAM_CHAT_ID`.

## 2. BingX
1. API Management → crea clave con permisos **Lectura + Trading de futuros**. **Sin retiros**.
2. Guarda `BINGX_API_KEY` y `BINGX_API_SECRET`.
3. Revisa el modo de posición de tu cuenta de futuros (por defecto *Hedge*): `POSITION_MODE=hedge|oneway`.
4. Si usas lista blanca de IP: Railway tiene IP dinámica; déjala sin restringir o usa un plan con IP estática.

## 3. GitHub
```bash
git init && git add . && git commit -m "granville bot"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/granville-bot.git
git push -u origin main
```
(`.env` está en `.gitignore`: nunca subas claves.)

## 4. Railway
1. New Project → Deploy from GitHub repo.
2. Variables: copia todas las de `.env.example` con tus valores.
3. Volumes → añade un volumen montado en `/data` (guarda `state.json` entre reinicios) y deja `DATA_DIR=/data` (el bot usa archivos `granville_*.json`, no pisa los de otros bots).
4. Deploy. Verás "Granville Bot iniciado" en Telegram.
5. Mantén **una sola réplica** (si no, abrirá operaciones duplicadas).

## Variables principales
| Variable | Qué hace |
|---|---|
| `DRY_RUN` | true = solo señales |
| `BINGX_DEMO` | true = cuenta demo VST |
| `SYMBOLS` | formato ccxt, ej. `BTC/USDT:USDT` |
| `BOOK` | 0/1/2/3 |
| `RISK_PCT` | % de capital arriesgado por operación |
| `MAX_LEV` / `LEVERAGE` | tope nocional/capital y apalancamiento en BingX |
| `MAX_POSITIONS` | posiciones simultáneas |

## Local
```bash
pip install -r requirements.txt
cp .env.example .env   # rellena
export $(grep -v '^#' .env | xargs) && python main.py
```

## Limitaciones conocidas
- El Pine recibido estaba **cortado** (terminaba dentro de la función HaP, sin las definiciones de G1–G8 ni el filtro bayesiano de "ventaja ★"). Las señales G1–G8 y el score HaP están **reconstruidos** a partir de las reglas de Granville y de los parámetros del script, por lo que pueden diferir del original.
- El filtro "ventaja ★" bayesiano se sustituye por el meta-labeling.
- El backtest no incluye funding pagado ni límite de posiciones simultáneas; si SL y TP caen en la misma vela asume SL.
- No probado contra BingX en vivo: valida en DRY_RUN y demo antes de usar dinero real. Revisa que SL/TP aparezcan bien en BingX tras la primera operación.
