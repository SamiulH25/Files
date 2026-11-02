"""Runs the real app on the live Wayland session against a sandbox, then exits and writes a report.

It uses its own window class. Send that class to a hidden workspace first so it can't take focus:
  hyprctl eval 'hl.window_rule({match={class="io.github.Files.WlCheck"}, workspace="special:filestest silent"})'
  /usr/bin/python3 tests/wayland_check.py REPORT
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
REPORT = sys.argv[1]
sys.argv = sys.argv[:1]

SANDBOX = tempfile.mkdtemp(prefix="files-wl-")
os.environ["XDG_STATE_HOME"] = os.path.join(SANDBOX, "state")
os.environ["_ZO_DATA_DIR"] = os.path.join(SANDBOX, "zoxide")
TREE = os.path.join(SANDBOX, "tree")
os.makedirs(os.path.join(TREE, "folder"))
with open(os.path.join(TREE, "note.txt"), "w") as f:
    f.write("hello")

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from filesapp.app import FilesApp

result = {"backend": None, "renderer": None, "errors": [], "show_items": None, "windows": 0}


class CheckApp(FilesApp):
    def __init__(self):
        super().__init__()
        self.set_application_id("io.github.Files.WlCheck")

    def do_activate(self):
        win = self.new_window(Gio.File.new_for_path(TREE))
        GLib.timeout_add(1500, self.check, win)

    def check(self, win):
        result["backend"] = type(Gdk.Display.get_default()).__name__
        surface = win.get_surface()
        renderer = win.get_renderer()
        result["renderer"] = type(renderer).__name__ if renderer else None
        result["surface"] = type(surface).__name__
        result["items"] = [i.get_name() for i in win.items()]
        name = self.get_dbus_connection().get_unique_name()
        # Talks to this process's own bus name, so Nautilus can never be started by the test.
        call = Gio.Subprocess.new(
            ["gdbus", "call", "--session", "--dest", name, "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowItems", f"['file://{TREE}/note.txt']", ""],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        call.communicate_utf8_async(None, None, self.called, None)
        return False

    def called(self, source, res, _data):
        _ok, out, _err = source.communicate_utf8_finish(res)
        result["show_items"] = out.strip()
        GLib.timeout_add(700, self.finish)

    def finish(self):
        windows = self.get_windows()
        result["windows"] = len(windows)
        newest = windows[0]
        result["selected_in_new_window"] = [i.get_name() for i in newest.selected_infos()]
        for window in list(windows):
            window.close()
        self.quit()
        return False


try:
    app = CheckApp()
    app.run([])
except Exception as e:
    result["errors"].append(repr(e))
finally:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    with open(REPORT, "w") as f:
        json.dump(result, f, indent=1)
