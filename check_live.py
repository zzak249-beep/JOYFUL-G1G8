"""Comprobación PREVIA a operar en real. No abre ni cierra nada.
    python check_live.py            (con las variables de entorno de Railway/.env cargadas)
Comprueba: claves, saldo de futuros, posiciones abiertas, modo de posición y si tu riesgo da un tamaño operable."""
import config as C


def main():
    import ccxt
    if not C.BINGX_KEY or not C.BINGX_SECRET:
        print("✗ Faltan BINGX_API_KEY / BINGX_API_SECRET")
        return
    ex = ccxt.bingx({"apiKey": C.BINGX_KEY, "secret": C.BINGX_SECRET, "enableRateLimit": True,
                     "options": {"defaultType": "swap"}})
    if C.DEMO:
        ex.set_sandbox_mode(True)
    print(f"Modo: {'DEMO (VST)' if C.DEMO else 'DINERO REAL'} · DRY_RUN={C.DRY_RUN} · estrategia {C.STRATEGY}")
    ex.load_markets()
    try:
        b = ex.fetch_balance().get("USDT", {})
        eq = float(b.get("total") or b.get("free") or 0)
        print(f"✓ Claves válidas · saldo de futuros: {eq:.2f} USDT (libre {float(b.get('free') or 0):.2f})")
    except Exception as e:
        print(f"✗ No pude leer el saldo: {e}\n  Revisa permisos (lectura + trading de futuros) y que el dinero esté en la cuenta de FUTUROS USDT-M.")
        return
    try:
        pos = [p for p in ex.fetch_positions() if float(p.get("contracts") or 0) > 0]
        print(f"{'⚠' if pos else '✓'} Posiciones abiertas ahora: {len(pos)}" + (f" ({', '.join(p['symbol'] for p in pos[:6])})" if pos else ""))
        if pos:
            print("  El bot solo gestiona lo que abre él. Si otro bot opera esta cuenta, usa una subcuenta propia.")
    except Exception as e:
        print(f"⚠ No pude leer posiciones: {e}")
    try:
        pm = ex.fetch_position_mode("BTC/USDT:USDT")
        print(f"Modo de posición detectado: {pm}  → POSITION_MODE={'hedge' if pm.get('hedged') else 'oneway'} (tienes: {C.POSITION_MODE})")
    except Exception:
        print(f"⚠ No pude detectar el modo de posición. Mira en BingX (Preferencias de futuros: Hedge o One-way) y pon POSITION_MODE=hedge|oneway (tienes: {C.POSITION_MODE})")

    print(f"\nConfig: riesgo {C.RISK_PCT}% · apalancamiento máx {C.MAX_LEV}x (en BingX {C.LEVERAGE}x) · máx posiciones {C.MAX_POSITIONS} · "
          f"máx por lado {C.MAX_SAME_DIR} · freno diario {C.DAILY_LOSS_PCT}%")
    print(f"Riesgo por operación ≈ {eq * C.RISK_PCT / 100:.2f} USDT. ¿Da un tamaño operable? (supone un stop del 1% del precio)")
    try:
        import universe
        syms = [s for s, _, _ in universe.build(ex)[:6]] if C.SCAN_ALL else C.SYMBOLS[:6]
    except Exception:
        syms = C.SYMBOLS[:6]
    for s in syms:
        try:
            m = ex.market(s)
            px = float(ex.fetch_ticker(s)["last"])
            qty = min(eq * C.RISK_PCT / 100 / (0.01 * px), eq * C.MAX_LEV / px)
            mn = m.get("limits", {}).get("amount", {}).get("min") or 0
            mc = m.get("limits", {}).get("cost", {}).get("min") or 0
            ok = qty >= mn and qty * px >= mc
            print(f"  {'✓' if ok else '✗'} {s:<22} tamaño ≈ {qty:.6g} (≈{qty * px:.1f} USDT) · mínimo {mn} / {mc} USDT"
                  + ("" if ok else "  ← por debajo del mínimo: sube el saldo o el riesgo"))
        except Exception as e:
            print(f"  ? {s}: {e}")
    print("\nSiguiente paso: DRY_RUN=false en Railway. Al arrancar debe llegar a Telegram 'Cuenta BingX conectada'.")


if __name__ == "__main__":
    main()
