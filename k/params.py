from __future__ import annotations
import json
import os
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'params.json')

class Params:

    def __init__(self, data: dict):
        self._data = data
        for key, val in data.items():
            setattr(self, key, Params(val) if isinstance(val, dict) else val)

    def flatten(self, prefix: str='') -> dict:
        out = {}
        for key, val in self._data.items():
            name = f'{prefix}{key}'
            if isinstance(val, dict):
                out.update(getattr(self, key).flatten(name + '.'))
            else:
                out[name] = val
        return out

    def as_dict(self) -> dict:
        return self._data

def load_params(path: str | None=None, overrides: dict | None=None) -> Params:
    path = path or os.environ.get('BOT_PARAMS_FILE') or DEFAULT_PATH
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    for dotted, val in (overrides or {}).items():
        node = data
        *parents, leaf = dotted.split('.')
        for part in parents:
            node = node[part]
        if leaf not in node:
            raise KeyError(f'unknown parameter {dotted}')
        node[leaf] = val
    return Params(data)
