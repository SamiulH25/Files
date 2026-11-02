"""Regenerates the README screenshots from made-up files, so no real paths or drive names can end up in them.

Run: /usr/bin/python3 tests/readme_shots.py docs/screenshots
HOME and all app data point into a sandbox, and the window lives on a private Broadway display.
"""
import faulthandler
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.abspath(sys.argv[1])

HOME = tempfile.mkdtemp(prefix="files-shots-")
DISPLAY = ":" + str(random.randrange(500, 900))
os.environ.update(
    HOME=HOME, XDG_CONFIG_HOME=f"{HOME}/.config", XDG_STATE_HOME=f"{HOME}/.local/state", XDG_CACHE_HOME=f"{HOME}/.cache",
    XDG_DATA_HOME=f"{HOME}/.local/share", _ZO_DATA_DIR=f"{HOME}/.local/share/zoxide",
    GDK_BACKEND="broadway", BROADWAY_DISPLAY=DISPLAY, GSK_RENDERER="cairo",
)
# Before the imports: PyGObject initialises GTK when Gtk is imported, and GTK needs the display by then.
daemon = subprocess.Popen(["gtk4-broadwayd", DISPLAY], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.4)

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk

from filesapp import APP_ID, jump, theme
from filesapp.app import FilesApp

THEMES = "/usr/share/omarchy/themes"
FONT = "JetBrainsMono Nerd Font"
SCALE = 2  # renders text sharp on high-density screens, since Broadway itself is 1x
DAY = 86400
FILES = {  # path: (size in bytes, age in days)
    "Workspace/notes.md": (1240, 0.1), "Workspace/todo.txt": (310, 0.4), "Workspace/budget-2026.csv": (18400, 2),
    "Workspace/report.pdf": (2400000, 3), "Workspace/setup.py": (820, 6), "Workspace/backup.tar.gz": (734000000, 9),
    "Workspace/talk.mp4": (88000000, 15), "Workspace/theme-song.mp3": (5200000, 40),
    "Workspace/.gitignore": (160, 90), "Workspace/.editorconfig": (480, 200), "Workspace/docs/notes.md": (900, 20),
}
FOLDERS = [".config", "Documents/thesis", "Downloads", "Music", "Pictures", "Projects/games", "Projects/tools/Files",
           "Projects/tools/toast", "Videos", "USB", "Workspace/.git", "Workspace/assets", "Workspace/docs", "Workspace/src"]
VISITS = {"Projects/tools": 40, "Workspace": 30, "Projects/tools/Files": 22, "Projects/tools/toast": 14, "Projects/games": 8,
          "Documents/thesis": 5}
SCENES = {  # picture name: (sky top, sky bottom, far hills, near hills)
    "alpine-morning": ("#27406b", "#f2b880", "#59708f", "#1f2c3f"), "desert-dusk": ("#3b1d4f", "#f47c4b", "#a2504a", "#3a1a2b"),
    "fjord": ("#0f3a52", "#9fd3c7", "#3f7f83", "#10303a"), "forest-rain": ("#4a5a58", "#c3cfc0", "#55755c", "#1f3525"),
    "harbour-lights": ("#101a3a", "#c0587e", "#3c3c6e", "#121530"), "meadow": ("#5aa6d6", "#f4eeb8", "#7fae5a", "#3b6b34"),
    "red-canyon": ("#6d2f2a", "#f3c078", "#b0553a", "#4c1f1a"), "snowfield": ("#7d97b8", "#eef2f6", "#c5d2df", "#7c8ea3"),
}


def make_home():
    for folder in FOLDERS:
        os.makedirs(f"{HOME}/{folder}", exist_ok=True)
    now = time.time()
    for path, (size, age) in FILES.items():
        full = f"{HOME}/{path}"
        with open(full, "wb") as f:
            f.truncate(size)  # sparse, so the sizes look real without taking the space
        os.utime(full, (now - age * DAY, now - age * DAY))
    os.symlink("../Projects/tools/Files", f"{HOME}/Workspace/current")  # relative: the row prints the target
    with open(f"{HOME}/.config/user-dirs.dirs", "w") as f:
        for key, name in (("DOWNLOAD", "Downloads"), ("DOCUMENTS", "Documents"), ("PICTURES", "Pictures"),
                          ("MUSIC", "Music"), ("VIDEOS", "Videos")):
            f.write(f'XDG_{key}_DIR="$HOME/{name}"\n')
    for path, count in VISITS.items():
        for _ in range(count):
            jump.add(f"{HOME}/{path}")
    for i, (name, colours) in enumerate(SCENES.items()):
        paint_scene(f"{HOME}/Pictures/{name}.png", colours, i)


def paint_scene(path, colours, seed):
    """Sky, sun and two ranges of hills. Original art, so the repo carries no image it has no rights to."""
    rng = random.Random(seed)
    w, h = 900, 600
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surface)

    def rgb(text):
        return tuple(int(text[i:i + 2], 16) / 255 for i in (1, 3, 5))

    sky = cairo.LinearGradient(0, 0, 0, h)
    sky.add_color_stop_rgb(0, *rgb(colours[0]))
    sky.add_color_stop_rgb(0.8, *rgb(colours[1]))
    cr.set_source(sky)
    cr.paint()
    cr.set_source_rgba(1, 0.95, 0.85, 0.85)
    cr.arc(rng.uniform(150, 750), rng.uniform(150, 280), rng.uniform(30, 60), 0, 2 * math.pi)
    cr.fill()
    for base, colour in ((0.62, colours[2]), (0.8, colours[3])):
        waves = [(rng.uniform(0.004, 0.02), rng.uniform(0, 6), rng.uniform(15, 55)) for _ in range(4)]
        cr.move_to(0, h)
        for x in range(0, w + 1, 4):
            cr.line_to(x, base * h + sum(a * math.sin(f * x + p) for f, p, a in waves))
        cr.line_to(w, h)
        cr.close_path()
        cr.set_source_rgb(*rgb(colour))
        cr.fill()
    surface.write_to_png(path)


class FakeMount:
    """Stands in for the real volume monitor's mounts, which would publish this machine's drive names."""

    def __init__(self, root):
        self.root = root

    def is_shadowed(self):
        return False

    def can_eject(self):
        return True

    def can_unmount(self):
        return True

    def get_name(self):
        return "USB Stick"

    def get_root(self):
        return Gio.File.new_for_path(self.root)


class FakeMonitor:
    def get_mounts(self):
        return [FakeMount(f"{HOME}/USB")]

    def get_volumes(self):
        return []


def pump(seconds):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


def wait_for(predicate, what, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pump(0.02)
        if predicate():
            return
    raise AssertionError(f"timed out waiting for {what}")


def redraw_icons(widget):
    """Each shot should match a fresh launch in that theme, and drawn icons keep their colours until they redraw."""
    if isinstance(widget, Gtk.DrawingArea):
        widget.queue_draw()
    child = widget.get_first_child()
    while child is not None:
        redraw_icons(child)
        child = child.get_next_sibling()


class Shooter:
    def __init__(self, app, win):
        self.app, self.win, self.wallpaper = app, win, Gdk.RGBA()

    def theme(self, name):
        palette = theme.load_palette(f"{THEMES}/{name}/colors.toml")
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", palette["mode"] != "light")
        self.app.css.load_from_string(theme.build_css(palette, FONT))
        self.wallpaper.parse(palette["background"])  # no compositor here to blend translucent areas with a real wallpaper

    def shot(self, name):
        win = self.win
        pump(1.0)  # lets the overlay blur and view transitions finish
        redraw_icons(win)  # only now: until the new CSS has settled they would redraw in the old colours
        pump(0.1)
        paintable = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        # A fresh paintable can draw nothing until the widget has finished its pending layout.
        for _ in range(40):
            probe = Gtk.Snapshot()
            paintable.snapshot(probe, w, h)
            if probe.to_node() is not None:
                break
            pump(0.05)
        snapshot = Gtk.Snapshot()
        snapshot.scale(SCALE, SCALE)
        snapshot.append_color(self.wallpaper, Graphene.Rect().init(0, 0, w, h))
        paintable.snapshot(snapshot, w, h)
        renderer = Gsk.CairoRenderer()
        renderer.realize_for_display(win.get_display())
        texture = renderer.render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, w * SCALE, h * SCALE))
        renderer.unrealize()
        texture.save_to_png(os.path.join(OUT, name + ".png"))


def capture(app):
    make_home()
    work = f"{HOME}/Workspace"  # not Home itself, whose breadcrumb would print the sandbox's parent folders
    win = app.new_window(Gio.File.new_for_path(work))
    win.set_default_size(1024, 760)
    win.volume_monitor = FakeMonitor()
    wait_for(lambda: len(win.items()) >= 10 and len(win.entries) >= 4, "home to load")
    win._rebuild_sidebar()
    s = Shooter(app, win)

    s.theme("tokyo-night")
    win.select_names(["notes.md"])
    s.shot("list")
    win.selection.unselect_all()

    win.open_jump("")
    s.shot("search-empty")
    win.jump_entry.set_text("to")
    s.shot("search")
    win.close_jump()

    win.start_job("copy", [Gio.File.new_for_path(f"{work}/notes.md")], Gio.File.new_for_path(f"{work}/docs"))
    wait_for(lambda: win.sheet_open, "conflict question")
    s.shot("conflict")
    win._on_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
    wait_for(lambda: not win.jobs, "job to stop")
    win._hide_toast()
    pump(2.0)  # a theme change during the blur's fade-out restarts it, which would catch the next shot blurred

    for name in ("catppuccin-latte", "gruvbox", "rose-pine"):
        s.theme(name)
        win.select_names(["notes.md"])
        s.shot(f"theme-{name}")
    win.selection.unselect_all()

    s.theme("tokyo-night")
    win.navigate(Gio.File.new_for_path(f"{HOME}/Pictures"))
    win.set_view("grid")
    wait_for(lambda: len(win.items()) == len(SCENES), "pictures to list")
    pump(3.0)  # thumbnails render on a worker thread
    s.shot("grid")


class ShotsApp(FilesApp):
    exit_code = 1

    def __init__(self):
        Gtk.Application.__init__(self, application_id=APP_ID + ".Shots", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.css = Gtk.CssProvider()
        self.theme_timer = 0

    def do_activate(self):
        self.hold()
        GLib.idle_add(self._run)

    def _run(self):
        try:
            capture(self)
            self.exit_code = 0
        except Exception:
            traceback.print_exc()
        self.quit()
        return False

    def _export_file_manager(self):
        pass


def main():
    faulthandler.dump_traceback_later(120, exit=True)
    os.makedirs(OUT, exist_ok=True)
    app = ShotsApp()
    try:
        app.run([])
    finally:
        daemon.kill()
        shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(app.exit_code)


main()
