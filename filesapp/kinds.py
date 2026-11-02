"""Maps files to a glyph and colour class. Glyphs are Nerd Font, which Omarchy ships as its default font."""
import os
from datetime import datetime

FOLDER = ""
FILE = ""
TEXT = ""
IMAGE = ""
VIDEO = ""
AUDIO = ""
ARCHIVE = ""
PDF = ""
CODE = ""
EXEC = ""
DOC = ""
SHEET = ""
SLIDES = ""
LINK = ""

HOME = ""
DOWNLOAD = ""
TRASH = ""
DRIVE = ""
USB = ""
BOOKMARK = ""
EJECT = ""
SORT_UP = ""
SORT_DOWN = ""
RECENT = ""

CODE_EXT = {
    "c", "h", "cc", "cpp", "hpp", "cs", "css", "scss", "go", "hs", "html", "java", "js", "jsx", "ts", "tsx",
    "kt", "lua", "php", "pl", "py", "rb", "rs", "sh", "bash", "zsh", "fish", "sql", "swift", "vue", "zig",
    "json", "jsonc", "toml", "yaml", "yml", "xml", "ini", "conf", "qml", "gd", "glsl", "nix",
}
ARCHIVE_EXT = {"zip", "tar", "gz", "bz2", "xz", "zst", "7z", "rar", "tgz", "deb", "rpm", "jar", "iso"}


def kind_of(name, mime, is_dir):
    """Returns (glyph, css class) for a file."""
    if is_dir:
        return FOLDER, "k-folder"
    mime = mime or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if mime.startswith("image/"):
        return IMAGE, "k-image"
    if mime.startswith("video/"):
        return VIDEO, "k-video"
    if mime.startswith("audio/"):
        return AUDIO, "k-audio"
    if mime == "application/pdf":
        return PDF, "k-pdf"
    if ext in ARCHIVE_EXT:
        return ARCHIVE, "k-archive"
    if ext in CODE_EXT:
        return CODE, "k-code"
    if "spreadsheet" in mime or ext in {"csv", "xls", "xlsx", "ods"}:
        return SHEET, "k-sheet"
    if "presentation" in mime or ext in {"ppt", "pptx", "odp"}:
        return SLIDES, "k-slides"
    if "wordprocessing" in mime or ext in {"doc", "docx", "odt", "rtf"}:
        return DOC, "k-doc"
    if mime in {"application/x-executable", "application/x-pie-executable", "application/vnd.appimage"}:
        return EXEC, "k-exec"
    if mime.startswith("text/") or ext in {"md", "txt", "log"}:
        return TEXT, "k-text"
    return FILE, "k-file"


def size_text(size):
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return ""


def when_text(ts, now=None):
    """Today shows the time, this year the date, older files the year."""
    if not ts:
        return ""
    now = now or datetime.now()
    then = datetime.fromtimestamp(ts)
    if then.date() == now.date():
        return then.strftime("%H:%M")
    if then.year == now.year:
        return then.strftime("%b %-d")
    return then.strftime("%Y")


def thumbable(mime):
    return (mime or "").startswith(("image/", "video/")) or mime == "application/pdf"


def plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def home_short(path):
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    return "~" + path[len(home):] if path.startswith(home + "/") else path
