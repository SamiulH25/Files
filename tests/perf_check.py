"""Measures a very large folder on the private test display. Run: tests/run_ui.sh is not needed, see README."""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SANDBOX = tempfile.mkdtemp(prefix="files-perf-", dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
os.environ["XDG_STATE_HOME"] = os.path.join(SANDBOX, "state")
os.environ["_ZO_DATA_DIR"] = os.path.join(SANDBOX, "zoxide")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from filesapp import APP_ID
from filesapp.app import FilesApp

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 50000


class PerfApp(FilesApp):
    def __init__(self):
        Gtk.Application.__init__(self, application_id=APP_ID + ".Perf", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.css = Gtk.CssProvider()
        self.theme_timer = 0

    def _export_file_manager(self):
        pass

    def do_activate(self):
        big = os.path.join(SANDBOX, "big")
        os.makedirs(big)
        for i in range(COUNT):
            open(os.path.join(big, f"file {i}.txt"), "w").close()
        os.makedirs(os.path.join(SANDBOX, "small"))

        win = self.new_window(Gio.File.new_for_path(os.path.join(SANDBOX, "small")))
        worst = [0.0]
        last = [time.monotonic()]

        def tick():
            now = time.monotonic()
            worst[0] = max(worst[0], now - last[0])
            last[0] = now
            return True

        GLib.timeout_add(5, tick)
        start = time.monotonic()
        win.navigate(Gio.File.new_for_path(big))

        def check():
            if win.sorted.get_n_items() == COUNT and not win.dirlist.is_loading() and win.sorted.get_pending() == 0:
                print(f"{COUNT} files listed and sorted in {time.monotonic() - start:.2f}s, "
                      f"longest UI freeze {worst[0] * 1000:.0f} ms")
                first = win.sorted.get_item(0).get_name()
                assert first == "file 0.txt", first
                assert win.sorted.get_item(10).get_name() == "file 10.txt", "natural order"
                t = time.monotonic()
                worst[0] = 0
                win.open_jump("file 4999")
                print(f"quick search opened in {(time.monotonic() - t) * 1000:.0f} ms, "
                      f"{len(win.jump_hits)} hits, top: {win.jump_hits[0].path}")
                win.close_jump()
                worst[0], last[0] = 0, time.monotonic()
                t = time.monotonic()
                win.selection.select_all()
                print(f"select all in {(time.monotonic() - t) * 1000:.0f} ms")
                self.quit()
                return False
            if time.monotonic() - start > 60:
                print("timed out", win.sorted.get_n_items(), win.sorted.get_pending())
                self.quit()
                return False
            return True

        GLib.timeout_add(20, check)


try:
    PerfApp().run([])
finally:
    shutil.rmtree(SANDBOX, ignore_errors=True)
