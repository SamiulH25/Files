"""Quick search over zoxide's database, ranked the way `z` ranks it in the terminal."""
import itertools
import os
import re
import subprocess
from collections import namedtuple

Hit = namedtuple("Hit", "kind path score")  # kind: "jump" (a zoxide dir) or "here" (a name in cwd)

URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def parse(text):
    """Parse `zoxide query --list --score` output into [(score, path)], best first."""
    entries = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            try:
                entries.append((float(parts[0]), parts[1]))
            except ValueError:
                pass
    return entries


def load(exclude=None):
    cmd = ["zoxide", "query", "--list", "--score"]
    if exclude:
        cmd += ["--exclude", exclude]
    try:
        # zoxide stats every entry, so a dead mount can stall it: callers run this off the UI thread.
        return parse(subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError):
        return []


def add(path):
    """Record a visit, so folders opened here also rank in the terminal's `z`."""
    try:
        subprocess.run(["zoxide", "add", "--", path], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def zoxide_match(keywords, path):
    """Port of zoxide's filter_by_keywords: terms in order, the last one in the final component."""
    if not keywords:
        return True
    path = path.lower()
    last = keywords[-1]
    idx = path.rfind(last)
    if idx < 0 or "/" in path[idx + len(last):]:
        return False
    path = path[:idx]
    for keyword in reversed(keywords[:-1]):
        idx = path.rfind(keyword)
        if idx < 0:
            return False
        path = path[:idx]
    return True


def positions(query, name):
    """Indexes of `name` that match `query`: a substring if possible, else a subsequence, else None."""
    query, low = query.lower(), name.lower()
    if not query:
        return []
    idx = low.find(query)
    if idx >= 0:
        return list(range(idx, idx + len(query)))
    found, at = [], 0
    for ch in query:
        at = low.find(ch, at)
        if at < 0:
            return None
        found.append(at)
        at += 1
    return found


def _rank_here(query, names, limit):
    query = query.lower()
    def tier(name):
        low = name.lower()
        return 0 if low.startswith(query) else 1 if query in low else 2
    matches = [n for n in names if positions(query, n) is not None]
    matches.sort(key=lambda n: (tier(n), len(n), n.lower()))
    return matches[:limit]


def search(query, entries, here=(), limit=9):
    """Terminal-identical zoxide hits first, then names in this folder, then fuzzy zoxide hits."""
    keywords = query.lower().split()
    if not keywords:
        return [Hit("jump", p, s) for s, p in entries[:limit]]
    exact, fuzzy = [], []
    joined = "".join(keywords)
    for score, path in entries:
        if zoxide_match(keywords, path):
            exact.append(Hit("jump", path, score))
        elif positions(joined, os.path.basename(path)) is not None:
            fuzzy.append(Hit("jump", path, score))
    hits = exact[:limit]
    hits += [Hit("here", n, 0.0) for n in _rank_here(query.strip(), here, 5)]
    return (hits + fuzzy)[:limit + 5]


def is_location(query):
    """True when the query is a path or URI to complete rather than a search."""
    return query.startswith(("/", "~", "./", "../")) or bool(URI.match(query))


def expand(query, cwd):
    if URI.match(query):
        return query
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(query))) + ("/" if query.endswith("/") else "")


def complete(query, cwd, show_hidden=True, limit=12):
    """Folders completing a typed path, as full paths."""
    if URI.match(query):
        return []
    path = expand(query, cwd)
    parent, prefix = os.path.split(path)
    low = prefix.lower()
    found = []
    try:
        with os.scandir(parent or "/") as it:
            # shortcut: only the first 5000 entries are scanned, raise it if completion misses folders.
            for entry in itertools.islice(it, 5000):
                name = entry.name
                if name.lower().startswith(low) and (show_hidden or low.startswith(".") or not name.startswith(".")):
                    if entry.is_dir():
                        found.append(entry.path)
    except OSError:
        return []
    found.sort(key=lambda p: (os.path.basename(p).startswith("."), p.lower()))
    return found[:limit]
