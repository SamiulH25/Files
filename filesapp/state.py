"""Remembers the last folder and view settings between runs."""
import json
import os

DEFAULTS = {
    "last_dir": None,
    "show_hidden": True,
    "view": "list",
    "sort": "name",
    "sort_desc": False,
    "width": 1180,
    "height": 760,
}


def _path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "files", "state.json")


def load():
    state = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return state


def save(state):
    path = _path()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)  # atomic, so a crash never leaves half a state file
    except OSError:
        pass


def nearest_existing(path):
    """The folder itself, or its closest surviving ancestor if it was deleted or unmounted."""
    path = path if isinstance(path, str) and os.path.isabs(path) else os.path.expanduser("~")
    while not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            return os.path.expanduser("~")
        path = parent
    return path
