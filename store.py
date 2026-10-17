import json


def load_state(path):
    """Carga solo entradas propias (dict con 'side' y 'sl_id'); ignora cualquier otro contenido del archivo."""
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and "side" in v and "sl_id" in v}


class Seen:
    """Señales ya enviadas (evita duplicados tras reiniciar/redesplegar)."""

    def __init__(self, path):
        import time as _t
        self.path, self._t, self.data = path, _t, {}
        try:
            with open(path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data = {k: v for k, v in d.items() if isinstance(v, (int, float))}
        except Exception:
            pass

    def seen(self, key): return key in self.data
    def add(self, key): self.data[key] = self._t.time()

    def save(self):
        import os
        cutoff = self._t.time() - 3 * 86400
        self.data = {k: v for k, v in self.data.items() if v >= cutoff}
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except Exception:
            pass
