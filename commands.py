"""Comandos de Telegram: /stats /semana /abiertas /estado /pausa /reanudar /exportar (solo desde tu chat)."""
import logging
import requests

log = logging.getLogger("commands")


def command_loop(token, chat_id, handlers, stop, on_conflict=None):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    offset, conflicts = None, 0
    while not stop.is_set():
        try:
            params = {"timeout": 25}
            if offset:
                params["offset"] = offset
            r = requests.get(url, params=params, timeout=40)
            if r.status_code == 409:
                # otro proceso lee los comandos de este token (u hay un webhook activo): no afecta a las señales ni a las operaciones
                conflicts += 1
                if conflicts == 1:
                    log.warning("Telegram 409: otro proceso lee los comandos de este bot (o hay un webhook activo). "
                                "Las señales y operaciones NO se ven afectadas; solo los comandos. Reintento con espera creciente.")
                if conflicts == 3 and on_conflict:
                    on_conflict()
                stop.wait(min(60 * 2 ** (conflicts - 1), 900))
                continue
            conflicts = 0
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                if str(m.get("chat", {}).get("id")) != str(chat_id):
                    continue
                text = (m.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                fn = handlers.get(text.split()[0].split("@")[0].lower())
                if fn:
                    fn()
        except Exception as e:
            log.debug("command_loop: %s", e)
            stop.wait(5)
