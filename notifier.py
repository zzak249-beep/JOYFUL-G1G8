import logging
import time
import requests
import config as C

log = logging.getLogger("telegram")


def _tag():
    return f"<b>[{C.BOT_NAME}]</b> " if C.BOT_TAG and getattr(C, "BOT_NAME", "") else ""


def notify(text: str):
    text = _tag() + text
    log.info(text.replace("\n", " | ")[:200])
    if not C.TG_TOKEN or not C.TG_CHAT:
        return False
    payload = {"chat_id": C.TG_CHAT, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    for attempt in range(4):
        try:
            r = requests.post(f"https://api.telegram.org/bot{C.TG_TOKEN}/sendMessage", json=payload, timeout=15)
            if r.status_code == 429:
                time.sleep(float(r.json().get("parameters", {}).get("retry_after", 3)) + 1)
                continue
            if r.ok:
                return True
            log.warning("Telegram %s %s", r.status_code, r.text[:200])
            return False
        except Exception as e:
            log.warning("Telegram error: %s", e)
            time.sleep(1 + attempt)
    return False


def send_document(path, caption=""):
    """Envía un archivo al chat (para /exportar)."""
    if not C.TG_TOKEN or not C.TG_CHAT:
        return False
    try:
        with open(path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{C.TG_TOKEN}/sendDocument",
                              data={"chat_id": C.TG_CHAT, "caption": (_tag() + caption)[:1000], "parse_mode": "HTML"},
                              files={"document": f}, timeout=60)
        return r.ok
    except Exception as e:
        log.warning("sendDocument: %s", e)
        return False
