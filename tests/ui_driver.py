"""Drives the real window in-process on a private Broadway display, so tests never touch the desktop.

Run through tests/run_ui.sh. Pass a directory as argv[1] to also save screenshots there.
"""
import faulthandler
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SANDBOX = tempfile.mkdtemp(prefix="files-ui-")
os.environ["XDG_STATE_HOME"] = os.path.join(SANDBOX, "state")
os.environ["_ZO_DATA_DIR"] = os.path.join(SANDBOX, "zoxide")
os.environ["XDG_CACHE_HOME"] = os.path.join(SANDBOX, "cache")

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk

from filesapp import APP_ID, jump, ops, state, theme, window as window_mod
from filesapp.app import FilesApp

SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
window_mod.BOOKMARKS = os.path.join(SANDBOX, "bookmarks")
TREE = os.path.join(SANDBOX, "tree")


def make_tree():
    os.makedirs(f"{TREE}/Projects/tools/Files")
    os.makedirs(f"{TREE}/Projects/games")
    os.makedirs(f"{TREE}/Documents")
    os.makedirs(f"{TREE}/.config")
    for name, text in {
        "notes.md": "x" * 1200, "main.py": "print(1)", "archive.tar.gz": "z" * 90000, "report.pdf": "%PDF",
        "song.mp3": "m" * 5000, "clip.mp4": "v", ".bashrc": "b", "data.csv": "1,2", "todo.txt": "t",
    }.items():
        with open(f"{TREE}/{name}", "w") as f:
            f.write(text)
    os.symlink(f"{TREE}/Projects", f"{TREE}/link-to-projects")
    for i, path in enumerate([f"{TREE}/Projects/tools", f"{TREE}/Projects", f"{TREE}/Projects/games",
                              f"{TREE}/Documents", f"{TREE}/Projects/tools/Files"]):
        for _ in range((5 - i) ** 2 * 3):
            jump.add(path)


def pump(seconds=0.25):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


def wait_for(predicate, what, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pump(0.02)
        if predicate():
            return
    raise AssertionError(f"timed out waiting for {what}")


def shot(win, name):
    if not SHOTS:
        return
    pump(0.35)
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
    # A wallpaper stand-in, so translucent areas read the way they will on the desktop.
    bg = Gdk.RGBA()
    bg.parse("#3a4a63")
    rect = Graphene.Rect().init(0, 0, w, h)
    snapshot.append_color(bg, rect)
    paintable.snapshot(snapshot, w, h)
    node = snapshot.to_node()
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(win.get_display())
    texture = renderer.render_texture(node, rect)
    renderer.unrealize()
    texture.save_to_png(os.path.join(SHOTS, name + ".png"))


def names(win):
    return [i.get_display_name() for i in win.items()]


def press(win, keyval, mods=0):
    win._on_key(None, keyval, 0, Gdk.ModifierType(mods))


def run_all(app):
    make_tree()
    win = app.new_window(Gio.File.new_for_path(TREE))
    win.set_default_size(1180, 760)
    wait_for(lambda: len(win.items()) >= 10, "folder to load")
    wait_for(lambda: len(win.entries) >= 4, "zoxide entries")
    results = []

    def check(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception:
            results.append((name, False, traceback.format_exc()))

    def t_lists_hidden_by_default():
        assert ".bashrc" in names(win) and ".config" in names(win)

    def t_folders_first_natural():
        got = names(win)
        dirs = [".config", "Documents", "link-to-projects", "Projects"]
        assert sorted(got[:4], key=str.lower) == sorted(dirs, key=str.lower), got
        assert got.index("todo.txt") > 3

    def t_toggle_hidden():
        win.activate_action("win.toggle-hidden", None)
        wait_for(lambda: ".bashrc" not in names(win), "hidden filter")
        assert state.load()["show_hidden"] is False
        wait_for(lambda: win.btn_hidden.get_child().value == 0.0, "eye shut")
        win.activate_action("win.toggle-hidden", None)
        wait_for(lambda: ".bashrc" in names(win), "hidden back")
        assert state.load()["show_hidden"] is True
        wait_for(lambda: win.btn_hidden.get_child().value == 1.0, "eye open")

    def t_navigate_remembers_and_records():
        before = {p: s for s, p in jump.load()}.get(f"{TREE}/Documents", 0)
        win.navigate(Gio.File.new_for_path(f"{TREE}/Documents"))
        wait_for(lambda: state.load()["last_dir"] == f"{TREE}/Documents", "last folder saved")
        wait_for(lambda: {p: s for s, p in jump.load()}.get(f"{TREE}/Documents", 0) > before, "zoxide visit recorded")
        win.go_up()
        wait_for(lambda: win.cwd.get_path() == TREE and win.selected_infos()
                 and win.selected_infos()[0].get_name() == "Documents", "selects folder we came from")
        win.go_back()
        assert win.cwd.get_path() == f"{TREE}/Documents"
        win.go_forward()
        assert win.cwd.get_path() == TREE
        win.selection.unselect_all()

    def t_type_to_jump_top_hit_matches_z():
        press(win, Gdk.KEY_f)
        assert win.jump_open and win.jump_entry.get_text() == "f"
        assert win.jump_hits[0].path == f"{TREE}/Projects/tools/Files", win.jump_hits
        win.close_jump()
        assert not win.jump_open
        win.open_jump("to")
        expected = jump.search("to", [e for e in win.entries if e[1] != TREE])[0].path
        assert win.jump_hits[0].path == expected
        win._jump_accept()
        wait_for(lambda: win.cwd.get_path() == expected, "jump navigation")
        win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "todo.txt" in names(win), "back home")

    def t_jump_keyboard_and_here_results():
        win.open_jump("tod")
        kinds = [h.kind for h in win.jump_hits]
        assert "here" in kinds, win.jump_hits
        press(win, Gdk.KEY_Down)
        press(win, Gdk.KEY_Up)
        assert win.jump_index == 0
        press(win, Gdk.KEY_Up)
        assert win.jump_index == len(win.jump_hits) - 1
        press(win, Gdk.KEY_Escape)
        assert not win.jump_open

    def t_location_mode():
        win.open_jump(f"{TREE}/Proj")
        assert win.jump_hits and win.jump_hits[0].kind == "path"
        press(win, Gdk.KEY_Tab)
        assert win.jump_entry.get_text().endswith("/Projects/"), win.jump_entry.get_text()
        win._jump_accept()
        wait_for(lambda: win.cwd.get_path().startswith(f"{TREE}/Projects"), "location")
        win.open_jump(f"{TREE}/todo.txt")
        win._jump_accept()
        wait_for(lambda: win.cwd.get_path() == TREE and [i.get_name() for i in win.selected_infos()] == ["todo.txt"],
                 "file path selects it")
        win.selection.unselect_all()

    def t_copy_paste_clipboard_roundtrip():
        os.makedirs(f"{TREE}/Documents", exist_ok=True)
        win.select_names(["todo.txt"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.clip_selection(False)
        win.navigate(Gio.File.new_for_path(f"{TREE}/Documents"))
        pump(0.1)
        win.paste()
        wait_for(lambda: os.path.exists(f"{TREE}/Documents/todo.txt"), "pasted file")
        assert os.path.exists(f"{TREE}/todo.txt")
        wait_for(lambda: "todo.txt" in names(win), "monitor picks up new file")
        win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "main.py" in names(win), "home list")

    def t_cut_paste_moves():
        win.select_names(["data.csv"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.clip_selection(True)
        win.navigate(Gio.File.new_for_path(f"{TREE}/Documents"))
        pump(0.1)
        win.paste()
        wait_for(lambda: os.path.exists(f"{TREE}/Documents/data.csv"), "moved file")
        assert not os.path.exists(f"{TREE}/data.csv")
        win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "main.py" in names(win), "home list")

    def t_rename_validates_and_refuses_clash():
        win.select_names(["main.py"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.rename_selection()
        pump(0.1)
        pop = find_rename_popover(win)
        entry, error = pop.get_child().get_first_child(), pop.get_child().get_last_child()
        assert entry.get_text() == "main.py"
        entry.set_text("notes.md")
        entry.emit("activate")
        assert error.get_visible() and "already exists" in error.get_label()
        assert os.path.exists(f"{TREE}/main.py")
        with open(f"{TREE}/notes.md") as f:
            assert f.read().startswith("x")
        entry.set_text("a/b")
        entry.emit("activate")
        assert "can't contain" in error.get_label()
        entry.set_text("app.py")
        entry.emit("activate")
        wait_for(lambda: os.path.exists(f"{TREE}/app.py"), "rename")
        wait_for(lambda: [i.get_name() for i in win.selected_infos()] == ["app.py"], "selection follows rename")
        pump(0.1)
        win.selection.unselect_all()

    def t_new_folder():
        win.prompt_new(True)
        pump(0.1)
        pop = find_rename_popover(win)
        entry = pop.get_child().get_first_child()
        entry.set_text("Made")
        entry.emit("activate")
        wait_for(lambda: os.path.isdir(f"{TREE}/Made"), "new folder")
        wait_for(lambda: "Made" in names(win), "shows up")
        win.selection.unselect_all()

    def t_conflict_sheet_never_overwrites():
        with open(f"{TREE}/Documents/app.py", "w") as f:
            f.write("old")
        win.start_job("copy", [Gio.File.new_for_path(f"{TREE}/app.py")], Gio.File.new_for_path(f"{TREE}/Documents"))
        wait_for(lambda: win.sheet_open, "conflict question")
        shot(win, "conflict")
        press(win, Gdk.KEY_Escape)  # Esc must mean stop
        wait_for(lambda: not win.jobs, "job to end")
        with open(f"{TREE}/Documents/app.py") as f:
            assert f.read() == "old"

    def t_permanent_delete_needs_confirmation():
        with open(f"{TREE}/doomed.txt", "w") as f:
            f.write("d")
        wait_for(lambda: "doomed.txt" in names(win), "file appears")
        win.select_names(["doomed.txt"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.delete_selection()
        assert win.sheet_open
        assert win.sheet_default == "cancel"
        press(win, Gdk.KEY_Escape)
        pump(0.2)
        assert os.path.exists(f"{TREE}/doomed.txt")
        win.delete_selection()
        win._sheet_answer("delete")
        wait_for(lambda: not os.path.exists(f"{TREE}/doomed.txt"), "delete")
        win.selection.unselect_all()

    def t_destructive_blocked_while_overlay_open():
        win.select_names(["app.py"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.open_jump("x")
        win.activate_action("win.trash", None)
        pump(0.2)
        assert os.path.exists(f"{TREE}/app.py")
        win.close_jump()
        win.selection.unselect_all()

    def t_sort_modes():
        win._on_sort_clicked(None, "size")
        wait_for(lambda: names(win)[names(win).index("archive.tar.gz") - 1] in
                 [".config", "Documents", "link-to-projects", "Made", "Projects"], "size sort")
        win._on_sort_clicked(None, "name")
        if state.load()["sort_desc"]:
            win._on_sort_clicked(None, "name")
        pump(0.1)

    def t_grid_view_keeps_selection():
        win.select_names(["app.py"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.set_view("grid")
        pump(0.2)
        assert win.stack.get_visible_child_name() == "grid" and len(win.selected_infos()) == 1
        win.set_view("list")
        assert state.load()["view"] == "list"
        win.selection.unselect_all()

    def t_deleted_cwd_lands_on_parent():
        os.makedirs(f"{TREE}/Ephemeral/inner")
        win.navigate(Gio.File.new_for_path(f"{TREE}/Ephemeral/inner"))
        pump(0.2)
        shutil.rmtree(f"{TREE}/Ephemeral")
        wait_for(lambda: win.cwd.get_path() == TREE or win.message.get_visible(), "reacts to deletion")
        if win.cwd.get_path() != TREE:
            win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "app.py" in names(win), "home list")

    def t_unreadable_and_missing_messages():
        before = state.load()["last_dir"]
        win.navigate(Gio.File.new_for_path("/root"))
        wait_for(lambda: win.message.get_visible() and "permission" in win.message.get_label(), "permission message")
        pump(0.2)
        assert state.load()["last_dir"] == before, "a folder that failed to open must not be remembered"
        assert "/root" not in {p for _, p in jump.load()}, "a folder that failed to open must not reach zoxide"
        win.navigate(Gio.File.new_for_path(f"{TREE}/does/not/exist"))
        wait_for(lambda: win.message.get_visible() and "doesn't exist" in win.message.get_label(), "missing message")
        win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "app.py" in names(win), "home list")
        assert not win.message.get_visible()

    def t_bookmarks_shared_format():
        win.selection.unselect_all()
        win.navigate(Gio.File.new_for_path(f"{TREE}/Documents"))
        win.toggle_bookmark()
        assert window_mod.read_bookmarks() == [(Gio.File.new_for_path(f"{TREE}/Documents").get_uri(), None)]
        win.toggle_bookmark()
        assert window_mod.read_bookmarks() == []
        win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "app.py" in names(win), "home list")

    def t_menus_build_and_drop_logic():
        infos = [i for i in win.items() if i.get_name() == "app.py"]
        assert win._build_menu(infos).get_n_items() > 0
        assert win._build_menu([]).get_n_items() > 0
        moved = []
        original = win.start_job
        win.start_job = lambda kind, files, dest=None, **kw: moved.append(kind)
        f = Gio.File.new_for_path(f"{TREE}/app.py")
        d = Gio.File.new_for_path(f"{TREE}/Documents")
        both = Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        win.handle_drop([f], d, 0, both)
        win.handle_drop([f], d, Gdk.ModifierType.CONTROL_MASK, both)
        win.handle_drop([f], d, 0, Gdk.DragAction.COPY)
        win.handle_drop([f], Gio.File.new_for_uri("trash:///"), 0, both)
        win.start_job = original
        assert moved == ["move", "copy", "copy", "trash"], moved

    def t_context_menu_opens():
        win._show_menu(win._view(), 40, 40)
        pump(0.1)
        assert win.menu.get_visible()
        win.menu.popdown()
        pump(0.1)

    def t_grid_shows_image_thumbnails():
        from gi.repository import GdkPixbuf
        os.makedirs(f"{TREE}/Pictures")
        for i, color in enumerate((0xd19a66ff, 0x56b6c2ff, 0xc678ddff, 0x98c379ff)):
            pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 640, 400)
            pixbuf.fill(color)
            pixbuf.savev(f"{TREE}/Pictures/photo {i}.png", "png", [], [])
        win.navigate(Gio.File.new_for_path(f"{TREE}/Pictures"))
        win.set_view("grid")

        def thumbs_showing():
            found = []

            def walk(widget):
                child = widget.get_first_child()
                while child is not None:
                    if isinstance(child, window_mod.Cell) and child.picture.get_visible() and child.picture.get_paintable():
                        found.append(child)
                    walk(child)
                    child = child.get_next_sibling()

            walk(win.gridview)
            return len(found) == 4

        wait_for(thumbs_showing, "image thumbnails", timeout=8)
        shot(win, "thumbnails")
        win.set_view("list")
        win.navigate(Gio.File.new_for_path(TREE))
        wait_for(lambda: "app.py" in names(win), "home list")

    def t_clipboard_interop_payload():
        win.select_names(["app.py"])
        wait_for(lambda: len(win.selected_infos()) == 1, "selection")
        win.clip_selection(True)
        formats = win.get_clipboard().get_formats()
        for mime in ("x-special/gnome-copied-files", "text/uri-list"):
            assert formats.contain_mime_type(mime), mime
        win.selection.unselect_all()

    def t_theme_swaps_for_every_palette():
        roots = ["/usr/share/omarchy/themes", os.path.expanduser("~/.config/omarchy/themes")]
        checked = 0
        worst = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                path = os.path.join(root, name, "colors.toml")
                if os.path.exists(path):
                    errors = []
                    provider = Gtk.CssProvider()
                    provider.connect("parsing-error", lambda _p, _s, e: errors.append(e.message))
                    provider.load_from_string(theme.build_css(theme.load_palette(path), "JetBrainsMono Nerd Font"))
                    assert not errors, (name, errors[:3])
                    palette = theme.load_palette(path)
                    text = theme._rgb(theme.secondary_text(palette))
                    for surface in ("background", "darker_background"):
                        ratio = theme.contrast(text, theme._rgb(palette[surface]))
                        worst.append((round(ratio, 2), name, surface))
                    checked += 1
        assert checked > 0
        worst.sort()
        print("secondary text contrast, themes checked:", checked, "worst:", worst[:3], flush=True)
        # foreground itself sets the ceiling, so a theme with a dim foreground can't reach 4.5.
        assert worst[0][0] >= 3.0, worst[:5]

    def t_bad_theme_value_cannot_inject_css():
        path = os.path.join(SANDBOX, "evil.toml")
        with open(path, "w") as f:
            f.write('accent = "red; } * { color: red"\nbackground = "#112233"\n')
        palette = theme.load_palette(path)
        assert palette["accent"] == theme.FALLBACK["accent"] and palette["background"] == "#112233"

    tests = [v for k, v in sorted(locals().items()) if k.startswith("t_")]
    order = [t_lists_hidden_by_default, t_folders_first_natural, t_toggle_hidden, t_navigate_remembers_and_records,
             t_type_to_jump_top_hit_matches_z, t_jump_keyboard_and_here_results, t_location_mode,
             t_copy_paste_clipboard_roundtrip, t_cut_paste_moves, t_rename_validates_and_refuses_clash, t_new_folder,
             t_conflict_sheet_never_overwrites, t_permanent_delete_needs_confirmation,
             t_destructive_blocked_while_overlay_open, t_sort_modes, t_grid_view_keeps_selection,
             t_deleted_cwd_lands_on_parent, t_unreadable_and_missing_messages, t_bookmarks_shared_format,
             t_menus_build_and_drop_logic, t_context_menu_opens, t_grid_shows_image_thumbnails, t_clipboard_interop_payload, t_theme_swaps_for_every_palette,
             t_bad_theme_value_cannot_inject_css]
    assert len(order) == len(tests), "a test is missing from the run order"
    for t in order:
        check(t.__name__[2:], t)
        pump(0.03)

    # A second window opened with no argument lands where the first left off.
    win.navigate(Gio.File.new_for_path(f"{TREE}/Documents"))
    wait_for(lambda: state.load()["last_dir"] == f"{TREE}/Documents", "last folder saved")
    second = app.new_window()
    check("reopen_restores_last_folder", lambda: _assert(second.cwd.get_path() == f"{TREE}/Documents"))
    second.close()
    win.navigate(Gio.File.new_for_path(TREE))
    wait_for(lambda: "app.py" in names(win), "home list")

    if SHOTS:
        win.select_names(["notes.md"])
        pump(0.2)
        shot(win, "list")
        win.selection.unselect_all()
        win.open_jump("")
        shot(win, "jump-empty")
        win.jump_entry.set_text("to")
        shot(win, "jump-query")
        win.close_jump()
        win.set_view("grid")
        shot(win, "grid")
        win.set_view("list")
        app.css.load_from_string(theme.build_css(theme.load_palette("/usr/share/omarchy/themes/flexoki-light/colors.toml"),
                                                 "JetBrainsMono Nerd Font"))
        win.select_names(["notes.md"])
        shot(win, "light")
        win.selection.unselect_all()
        win.open_jump("to")
        shot(win, "light-jump")
        win.close_jump()

    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name)
        if detail:
            print(detail)
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    app.exit_code = 1 if failed else 0
    app.release()
    app.quit()
    return False


def _assert(condition):
    assert condition


def find_rename_popover(win):
    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Popover) and "rename" in child.get_css_classes() and child.get_visible():
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(win)
    assert found, "rename popover not open"
    return found[-1]


class TestApp(FilesApp):
    exit_code = 2

    def __init__(self):
        Gtk.Application.__init__(self, application_id=APP_ID + ".Test", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.css = Gtk.CssProvider()
        self.theme_timer = 0

    def do_activate(self):
        self.hold()  # the window only appears once run_all starts, and a windowless app would exit first
        GLib.idle_add(run_all, self)

    def _export_file_manager(self):
        pass


def main():
    faulthandler.dump_traceback_later(60, exit=True)
    app = TestApp()
    try:
        app.run([])
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(app.exit_code)


main()
