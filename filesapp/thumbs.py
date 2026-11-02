"""Thumbnails, stored in the shared freedesktop cache so other apps reuse them."""
import hashlib
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

SIZE = 256
MAX_MEMORY = 400
# shortcut: images over 256 MB show a glyph, raise this if huge scans need previews.
MAX_IMAGE_BYTES = 256_000_000
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "thumbnails", "large")


def cache_path(uri):
    return os.path.join(CACHE, hashlib.md5(uri.encode()).hexdigest() + ".png")


def _render(path, mime, out):
    """Writes a PNG to `out`, or raises."""
    if mime.startswith("image/"):
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            raise ValueError("too large")
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, SIZE, SIZE, True)
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
        pixbuf.savev(out, "png", [], [])
    elif mime.startswith("video/") and shutil.which("ffmpegthumbnailer"):
        subprocess.run(["ffmpegthumbnailer", "-i", path, "-o", out, "-s", str(SIZE), "-t", "15%"],
                       check=True, timeout=20, capture_output=True)
    elif mime == "application/pdf" and shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", "1", "-l", "1", "-scale-to", str(SIZE),
                        path, out[:-4]], check=True, timeout=20, capture_output=True)
    else:
        raise ValueError("no thumbnailer")


def load_or_create(path, uri, mtime, mime):
    """Returns a Gdk.Texture from the cache, creating the cached thumbnail if it is missing or stale."""
    cached = cache_path(uri)
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(cached)
        if pixbuf.get_option("tEXt::Thumb::MTime") == str(mtime):
            return Gdk.Texture.new_for_pixbuf(pixbuf)
    except GLib.Error:
        pass
    with tempfile.TemporaryDirectory(prefix="files-thumb-") as tmp:
        out = os.path.join(tmp, "t.png")
        _render(path, mime, out)
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(out)
        os.makedirs(CACHE, mode=0o700, exist_ok=True)
        part = f"{cached}.{os.getpid()}.{threading.get_ident()}.part"
        pixbuf.savev(part, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime"], [uri, str(mtime)])
        os.chmod(part, 0o600)
        os.replace(part, cached)
    return Gdk.Texture.new_for_pixbuf(pixbuf)


class Thumbnailer:
    """Newest request first, so the cells on screen win while scrolling through a big folder."""

    def __init__(self, workers=3):
        self._queue = queue.LifoQueue()
        self._memory = OrderedDict()
        self._failed = set()
        self._wanted = {}
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True).start()

    def request(self, path, uri, mtime, mime, callback):
        """Calls callback(texture) on the main loop. Returns a token for cancel(), or None if already done."""
        key = (uri, mtime)
        if key in self._failed:
            return None
        texture = self._memory.get(key)
        if texture is not None:
            self._memory.move_to_end(key)
            callback(texture)
            return None
        token = object()
        self._wanted[token] = True
        self._queue.put((token, key, path, uri, mtime, mime, callback))
        return token

    def cancel(self, token):
        self._wanted.pop(token, None)

    def _work(self):
        while True:
            token, key, path, uri, mtime, mime, callback = self._queue.get()
            if token not in self._wanted:
                continue
            try:
                texture = load_or_create(path, uri, mtime, mime)
            except (GLib.Error, OSError, ValueError, subprocess.SubprocessError):
                GLib.idle_add(self._failed.add, key)
                continue
            GLib.idle_add(self._deliver, token, key, texture, callback)

    def _deliver(self, token, key, texture, callback):
        self._memory[key] = texture
        while len(self._memory) > MAX_MEMORY:
            self._memory.popitem(last=False)
        if self._wanted.pop(token, None):
            callback(texture)
        return False
