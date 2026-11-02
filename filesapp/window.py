"""The main window."""
import math
import os
import subprocess
import threading

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango

from . import icons, jump, kinds, ops, state, thumbs

ATTRS = ",".join([
    "standard::name", "standard::display-name", "standard::type", "standard::size",
    "standard::is-hidden", "standard::is-backup", "standard::is-symlink", "standard::symlink-target",
    "standard::fast-content-type", "time::modified", "access::can-write", "trash::orig-path",
    "id::filesystem",
])
TRASH_URI = "trash:///"
BOOKMARKS = os.path.expanduser("~/.config/gtk-3.0/bookmarks")
HOME = os.path.expanduser("~")
MOD_MASK = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
SUMMARY_LIMIT = 5000

shared_thumbs = None


def glyph_label(glyph, *classes):
    label = Gtk.Label(label=glyph)
    label.add_css_class("glyph")
    for c in classes:
        label.add_css_class(c)
    return label


def tool_button(painter, tooltip, action=None):
    button = Gtk.Button(child=icons.Icon(painter), tooltip_text=tooltip, css_classes=["tool"], valign=Gtk.Align.CENTER)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    if action:
        button.set_action_name(action)
    return button


class Cell(Gtk.Box):
    """A list row or grid cell. `li` tells click handlers which item is under the pointer."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.li = None
        self.info = None
        self.token = None


class FilesWindow(Gtk.ApplicationWindow):
    def __init__(self, app, start=None, select=None):
        super().__init__(application=app, title="Files", decorated=False)
        global shared_thumbs
        shared_thumbs = shared_thumbs or thumbs.Thumbnailer()
        self.add_css_class("files")
        self.state = state.load()
        self.set_default_size(self.state["width"], self.state["height"])

        self.cwd = None
        self.back_stack, self.forward_stack = [], []
        self.entries = []        # zoxide's list as [(score, path)], best first
        self.sort_keys = {}
        self.pending_select = None
        self.pending_visit = None
        self.jump_open = False
        self.sheet_open = False
        self.sheet_queue = []
        self.jump_hits = []
        self.jump_index = 0
        self.jobs = {}
        self.toast_timer = 0
        self.clipboard_cut = False

        self.dirlist = Gtk.DirectoryList.new(ATTRS, None)
        self.dirlist.set_monitored(True)
        self.filter = Gtk.CustomFilter.new(self._filter_item)
        self.filtered = Gtk.FilterListModel(model=self.dirlist, filter=self.filter, incremental=True)
        self.sorter = Gtk.CustomSorter.new(self._compare)
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.sorter, incremental=True)
        self.selection = Gtk.MultiSelection(model=self.sorted)

        self._build_actions()
        self._build_ui()

        self.dirlist.connect("notify::loading", self._on_loading)
        self.dirlist.connect("notify::error", self._on_error)
        self.selection.connect("selection-changed", lambda *_: self._update_status())
        self.sorted.connect("items-changed", lambda *_: self._on_items_changed())

        monitor = Gio.VolumeMonitor.get()
        for signal in ("mount-added", "mount-removed", "mount-changed", "volume-added", "volume-removed"):
            monitor.connect(signal, lambda *_: self._rebuild_sidebar())
        self.volume_monitor = monitor

        self.connect("close-request", self._on_close)
        self._refresh_entries()
        self.navigate(start or Gio.File.new_for_path(state.nearest_existing(self.state["last_dir"])),
                      record=False, select=select)

    # ---- construction ----

    def _action(self, name, handler, accels=(), parameter=None, guarded=True):
        action = Gio.SimpleAction.new(name, GLib.VariantType.new(parameter) if parameter else None)

        def run(_action, param):
            if guarded and (self.jump_open or self.sheet_open):
                return
            handler(param) if parameter else handler()

        action.connect("activate", run)
        self.add_action(action)
        if accels:
            self.get_application().set_accels_for_action("win." + name, list(accels))
        return action

    def _build_actions(self):
        a = self._action
        a("open", self.open_selection)
        a("open-with", self._open_with, parameter="s")
        a("back", self.go_back, ["<Alt>Left"])
        a("forward", self.go_forward, ["<Alt>Right"])
        a("up", self.go_up, ["BackSpace", "<Alt>Up"])
        a("home", lambda: self.navigate(Gio.File.new_for_path(HOME)), ["<Alt>Home"])
        a("refresh", self.refresh, ["F5", "<Ctrl>r"])
        a("toggle-hidden", self.toggle_hidden, ["<Ctrl>h"])
        a("view-list", lambda: self.set_view("list"), ["<Ctrl>1"])
        a("view-grid", lambda: self.set_view("grid"), ["<Ctrl>2"])
        a("jump", lambda: self.open_jump(""), ["<Ctrl>k", "<Ctrl>f", "<Ctrl>p"])
        a("location", self._open_location, ["<Ctrl>l"])
        a("copy", lambda: self.clip_selection(False), ["<Ctrl>c"])
        a("cut", lambda: self.clip_selection(True), ["<Ctrl>x"])
        a("paste", self.paste, ["<Ctrl>v"])
        a("select-all", lambda: self.selection.select_all(), ["<Ctrl>a"])
        a("rename", self.rename_selection, ["F2"])
        a("trash", self.trash_selection, ["Delete"])
        a("delete", self.delete_selection, ["<Shift>Delete"])
        a("restore", self.restore_selection)
        a("empty-trash", self.empty_trash)
        a("new-folder", lambda: self.prompt_new(True), ["<Ctrl><Shift>n"])
        a("new-file", lambda: self.prompt_new(False))
        a("terminal", self.open_terminal, ["F4"])
        a("copy-path", self.copy_path, ["<Ctrl><Shift>c"])
        a("bookmark", self.toggle_bookmark, ["<Ctrl>d"])
        a("new-window", lambda: self.get_application().new_window(self.cwd), ["<Ctrl>n"], guarded=False)
        a("close", self.close, ["<Ctrl>w"], guarded=False)
        a("quit", lambda: self.get_application().quit(), ["<Ctrl>q"], guarded=False)

    def _build_ui(self):
        self.root = Gtk.Overlay()
        self.set_child(self.root)
        self.content = Gtk.Box(css_classes=["content"], hexpand=True, vexpand=True)
        self.root.set_child(self.content)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side_scroll = Gtk.ScrolledWindow(child=self.sidebar, css_classes=["sidebar"],
                                         hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=False)
        side_scroll.set_size_request(232, -1)
        self.content.append(side_scroll)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["main"], hexpand=True)
        self.content.append(main)

        # Header: ancestors as a quiet trail, the current folder as the headline.
        header = Gtk.Box(css_classes=["header"], spacing=6)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.crumbs = Gtk.Box()
        self.title = Gtk.Label(xalign=0, css_classes=["title"], ellipsize=Pango.EllipsizeMode.MIDDLE)
        titles.append(self.crumbs)
        titles.append(self.title)
        header.append(titles)

        tools = Gtk.Box(valign=Gtk.Align.END, spacing=6)
        self.btn_back = tool_button(icons.back, "Back (Alt+Left)", "win.back")
        self.btn_forward = tool_button(icons.forward, "Forward (Alt+Right)", "win.forward")
        self.btn_up = tool_button(icons.up, "Parent folder (Backspace)", "win.up")
        self.btn_hidden = tool_button(icons.eye, "Show hidden files (Ctrl+H)", "win.toggle-hidden")
        self.btn_list = tool_button(icons.list_view, "List view (Ctrl+1)", "win.view-list")
        self.btn_grid = tool_button(icons.grid_view, "Grid view (Ctrl+2)", "win.view-grid")
        self.btn_jump = tool_button(icons.search, "Quick search (start typing, or Ctrl+K)", "win.jump")
        for buttons in ((self.btn_back, self.btn_forward, self.btn_up), (self.btn_hidden,),
                        (self.btn_list, self.btn_grid), (self.btn_jump,)):
            group = Gtk.Box(css_classes=["tool-group"])
            for b in buttons:
                group.append(b)
            tools.append(group)
        header.append(tools)
        main.append(header)

        self.columns = Gtk.Box(css_classes=["columns"], spacing=12)
        self.col_buttons = {}
        for key, label, width in (("name", "Name", 0), ("size", "Size", 9), ("modified", "Modified", 8)):
            button = Gtk.Button(css_classes=["column"], hexpand=key == "name", tooltip_text=f"Sort by {label.lower()}")
            text = Gtk.Label(label=label, xalign=0 if key == "name" else 1, width_chars=width)
            button.set_child(text)
            button.connect("clicked", self._on_sort_clicked, key)
            self.col_buttons[key] = (button, text, label)
            self.columns.append(button)
        main.append(self.columns)

        # Views share one selection, so switching keeps what's selected.
        list_factory = Gtk.SignalListItemFactory()
        list_factory.connect("setup", self._row_setup)
        list_factory.connect("bind", self._row_bind)
        list_factory.connect("unbind", self._cell_unbind)
        self.listview = Gtk.ListView(model=self.selection, factory=list_factory, enable_rubberband=True)
        grid_factory = Gtk.SignalListItemFactory()
        grid_factory.connect("setup", self._grid_setup)
        grid_factory.connect("bind", self._grid_bind)
        grid_factory.connect("unbind", self._cell_unbind)
        self.gridview = Gtk.GridView(model=self.selection, factory=grid_factory, enable_rubberband=True,
                                     min_columns=2, max_columns=30)
        self.bound_cells = set()

        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        for name, view in (("list", self.listview), ("grid", self.gridview)):
            view.connect("activate", self._on_activate)
            scroll = Gtk.ScrolledWindow(child=view, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self.stack.add_named(scroll, name)
            clicks = Gtk.GestureClick(button=0)
            clicks.connect("pressed", self._on_view_click, view)
            view.add_controller(clicks)
        self.stack.add_controller(self._drop_target(lambda: self.cwd))

        self.message = Gtk.Label(css_classes=["empty"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                 wrap=True, justify=Gtk.Justification.CENTER, visible=False, can_target=False)
        view_overlay = Gtk.Overlay(child=self.stack)
        view_overlay.add_overlay(self.message)
        self.view_overlay = view_overlay
        main.append(view_overlay)

        self.jobs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(self.jobs_box)

        status = Gtk.Box(css_classes=["status"])
        self.status_left = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.status_right = Gtk.Label(xalign=1)
        status.append(self.status_left)
        status.append(self.status_right)
        main.append(status)

        self.menu = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        self.menu.set_parent(self.view_overlay)

        self._build_jump()
        self._build_sheet()
        self._build_toast()

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        mouse = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        mouse.connect("pressed", self._on_mouse_nav)
        self.add_controller(mouse)

        self.stack.set_visible_child_name(self.state["view"] if self.state["view"] in ("list", "grid") else "list")
        self._sync_tools()
        self._sync_columns()

    def _build_jump(self):
        self.jump = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["jump"], visible=False)
        self.jump_entry = Gtk.Text(css_classes=["jump-entry"], placeholder_text="Go to…", hexpand=True)
        self.jump_entry.connect("changed", lambda *_: self._on_jump_changed())
        self.jump_entry.connect("activate", lambda *_: self._jump_accept())
        self.jump.append(self.jump_entry)
        self.jump.append(Gtk.Box(css_classes=["jump-rule"]))
        self.jump_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.jump.append(Gtk.ScrolledWindow(child=self.jump_list, vexpand=True,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER))
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_jump_backdrop)
        self.jump.add_controller(click)
        self.root.add_overlay(self.jump)

    def _build_sheet(self):
        self.scrim = Gtk.Box(css_classes=["scrim"], visible=False)
        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sheet"],
                             halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
        self.sheet_title = Gtk.Label(xalign=0, css_classes=["sheet-title"], wrap=True, max_width_chars=60)
        self.sheet_body = Gtk.Label(xalign=0, css_classes=["sheet-body"], wrap=True, max_width_chars=64,
                                    wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.sheet_check = Gtk.CheckButton(label="Do this for all remaining conflicts", margin_top=14)
        self.sheet_actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=18)
        for w in (self.sheet_title, self.sheet_body, self.sheet_check, self.sheet_actions):
            self.sheet.append(w)
        self.scrim.append(self.sheet)
        self.root.add_overlay(self.scrim)

    def _build_toast(self):
        self.toast = Gtk.Revealer(halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_UP, can_target=True)
        box = Gtk.Box(css_classes=["toast"], spacing=12)
        self.toast_label = Gtk.Label(wrap=True, max_width_chars=70)
        self.toast_action = Gtk.Button(valign=Gtk.Align.CENTER)
        self.toast_action.connect("clicked", self._on_toast_action)
        self.toast_handler = None
        box.append(self.toast_label)
        box.append(self.toast_action)
        self.toast.set_child(box)
        self.root.add_overlay(self.toast)
        self.root.set_measure_overlay(self.toast, False)

    # ---- model ----

    def _filter_item(self, info, *_):
        return self.state["show_hidden"] or not (info.get_is_hidden() or info.get_is_backup())

    def _sort_key(self, name):
        key = self.sort_keys.get(name)
        if key is None:
            key = self.sort_keys[name] = GLib.utf8_collate_key_for_filename(name, -1)
        return key

    def _compare(self, a, b, *_):
        a_dir = a.get_file_type() == Gio.FileType.DIRECTORY
        b_dir = b.get_file_type() == Gio.FileType.DIRECTORY
        if a_dir != b_dir:
            return -1 if a_dir else 1
        mode = self.state["sort"]
        result = 0
        if mode == "size" and not a_dir:
            result = a.get_size() - b.get_size()
        elif mode == "modified":
            result = a.get_attribute_uint64("time::modified") - b.get_attribute_uint64("time::modified")
        if result:
            result = (result > 0) - (result < 0)
            return -result if self.state["sort_desc"] else result
        ka, kb = self._sort_key(a.get_display_name()), self._sort_key(b.get_display_name())
        result = (ka > kb) - (ka < kb)
        # Ties in size or date stay in name order, so rows never shuffle between refreshes.
        return -result if mode == "name" and self.state["sort_desc"] else result

    @staticmethod
    def file_of(info):
        return info.get_attribute_object("standard::file")

    def items(self):
        return [self.sorted.get_item(i) for i in range(self.sorted.get_n_items())]

    def selected_infos(self):
        bits = self.selection.get_selection()
        return [self.selection.get_item(bits.get_nth(i)) for i in range(bits.get_size())]

    def selected_files(self):
        return [self.file_of(i) for i in self.selected_infos()]

    @property
    def in_trash(self):
        return self.cwd is not None and self.cwd.get_uri_scheme() == "trash"

    # ---- navigation ----

    def navigate(self, file, record=True, select=None):
        if self.cwd is not None and file.equal(self.cwd):
            self.select_names(select)
            return
        if record and self.cwd is not None:
            self.back_stack.append(self.cwd)
            self.forward_stack.clear()
        self.cwd = file
        self.sort_keys.clear()
        self.pending_select = select
        self.message.set_visible(False)
        self.dirlist.set_file(file)
        self.selection.unselect_all()
        self._render_header()
        self._rebuild_sidebar()
        self._sync_tools()
        self._update_free_space()
        self._update_status()
        self.set_title(f"{self._display_name(file)} - Files")

        self.pending_visit = file.get_path() if not self.in_trash else None

    def _remember_visit(self):
        """Runs once a folder has loaded, so failed visits never become the last folder or reach zoxide."""
        path, self.pending_visit = self.pending_visit, None
        if path:
            self.state["last_dir"] = path
            state.save(self.state)
            threading.Thread(target=self._record_visit, args=(path,), daemon=True).start()

    def _record_visit(self, path):
        jump.add(path)
        self._refresh_entries()

    def _refresh_entries(self):
        def work():
            entries = jump.load()
            GLib.idle_add(self._set_entries, entries)
        threading.Thread(target=work, daemon=True).start()

    def _set_entries(self, entries):
        self.entries = entries
        self._rebuild_sidebar()
        return False

    def go_back(self):
        if self.back_stack:
            came_from = self.cwd
            target = self.back_stack.pop()
            self.forward_stack.append(came_from)
            self.navigate(target, record=False, select=self._child_on_path(target, came_from))

    def go_forward(self):
        if self.forward_stack:
            came_from = self.cwd
            target = self.forward_stack.pop()
            self.back_stack.append(came_from)
            self.navigate(target, record=False, select=self._child_on_path(target, came_from))

    @staticmethod
    def _child_on_path(parent, child):
        return [child.get_basename()] if child.get_parent() and child.get_parent().equal(parent) else None

    def go_up(self):
        parent = self.cwd.get_parent() if self.cwd else None
        if parent is not None:
            self.navigate(parent, select=[self.cwd.get_basename()])
        elif self.in_trash:
            self.navigate(Gio.File.new_for_path(HOME))

    def refresh(self):
        current = self.cwd
        names = [i.get_name() for i in self.selected_infos()]
        self.dirlist.set_file(None)
        self.pending_select = names
        self.dirlist.set_file(current)
        self._update_free_space()

    def open_location(self, text):
        """Goes to a typed path or URI. A file opens its folder with the file selected."""
        target = jump.expand(text, self.cwd.get_path() or HOME)
        if jump.URI.match(target):
            self.navigate(Gio.File.new_for_uri(target))
        elif os.path.isfile(target):
            self.navigate(Gio.File.new_for_path(os.path.dirname(target)), select=[os.path.basename(target)])
        else:
            self.navigate(Gio.File.new_for_path(target.rstrip("/") or "/"))

    def _on_loading(self, *_):
        if not self.dirlist.is_loading():
            if self.dirlist.get_error() is None:
                self._remember_visit()
                self.select_names(self.pending_select)
                self.pending_select = None
                self._focus_view()
            self._update_message()
        self._update_status()

    def _on_error(self, *_):
        error = self.dirlist.get_error()
        if error is None:
            return
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_MOUNTED):
            self.cwd.mount_enclosing_volume(Gio.MountMountFlags.NONE, Gtk.MountOperation.new(self), None,
                                            self._on_mounted, self.cwd)
            self.message.set_label("Connecting…")
            self.message.set_visible(True)
            return
        self._update_message()

    def _on_mounted(self, source, result, file):
        try:
            source.mount_enclosing_volume_finish(result)
            self.dirlist.set_file(None)
            self.dirlist.set_file(file)
        except GLib.Error as e:
            self.message.set_label(f"Couldn't connect.\n{e.message}")
            self.message.set_visible(True)

    def _on_items_changed(self):
        if not self.dirlist.is_loading():
            self._update_message()
        self._update_status()
        if self.cwd and self.sorted.get_n_items() == 0 and self.cwd.get_path() \
                and not self.dirlist.is_loading() and not os.path.isdir(self.cwd.get_path()):
            # The open folder was deleted or unmounted: land on what's left of its path.
            self.navigate(Gio.File.new_for_path(state.nearest_existing(self.cwd.get_path())), record=False)

    def _update_message(self):
        error = self.dirlist.get_error()
        if error is not None:
            if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.PERMISSION_DENIED):
                text = "You don't have permission to open this folder."
            elif error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                text = "This folder doesn't exist.\nPress Backspace to go up, or start typing to go somewhere else."
            else:
                text = f"Couldn't open this folder.\n{error.message}"
        elif self.sorted.get_n_items() == 0 and not self.dirlist.is_loading():
            if self.in_trash:
                text = "Trash is empty."
            elif self.dirlist.get_n_items() > 0:
                text = "Only hidden files here. Press Ctrl+H to show them."
            else:
                text = "Nothing here yet. Press Ctrl+Shift+N to make a folder."
        else:
            text = ""
        self.message.set_label(text)
        self.message.set_visible(bool(text))

    def select_names(self, names):
        """Selects files by name and scrolls to the first. Retries while the model is still filling."""
        if not names:
            return
        wanted = set(names)
        attempts = [0]

        def attempt():
            bits = Gtk.Bitset.new_empty()
            first = None
            for i in range(self.sorted.get_n_items()):
                if self.sorted.get_item(i).get_name() in wanted:
                    bits.add(i)
                    first = i if first is None else first
            if first is None or bits.get_size() < len(wanted):
                attempts[0] += 1
                if attempts[0] < 25:
                    return True
                if first is None:
                    return False
            self.selection.set_selection(bits, Gtk.Bitset.new_range(0, self.sorted.get_n_items()))
            self._view().scroll_to(first, Gtk.ListScrollFlags.FOCUS, None)
            return False

        if attempt():
            GLib.timeout_add(40, attempt)

    def _view(self):
        return self.listview if self.stack.get_visible_child_name() == "list" else self.gridview

    def _focus_view(self):
        if self.jump_open or self.sheet_open:
            return
        view = self._view()
        if self.selection.get_selection().get_size() == 0 and self.sorted.get_n_items() > 0:
            view.scroll_to(0, Gtk.ListScrollFlags.FOCUS, None)
        view.grab_focus()

    # ---- settings ----

    def toggle_hidden(self):
        self.state["show_hidden"] = not self.state["show_hidden"]
        state.save(self.state)
        self.filter.changed(Gtk.FilterChange.LESS_STRICT if self.state["show_hidden"] else Gtk.FilterChange.MORE_STRICT)
        self._sync_tools()
        self._update_status()
        self._update_message()

    def set_view(self, view):
        focused = self._view()
        self.state["view"] = view
        state.save(self.state)
        self.stack.set_visible_child_name(view)
        self._sync_tools()
        if focused.get_focus_child() or focused.has_focus():
            self._view().grab_focus()

    def _on_sort_clicked(self, _button, key):
        if self.state["sort"] == key:
            self.state["sort_desc"] = not self.state["sort_desc"]
        else:
            self.state["sort"] = key
            self.state["sort_desc"] = key != "name"  # biggest and newest first is what people mean
        state.save(self.state)
        self.sorter.changed(Gtk.SorterChange.DIFFERENT)
        self._sync_columns()

    def _sync_columns(self):
        for key, (button, text, label) in self.col_buttons.items():
            on = self.state["sort"] == key
            mark = f" {kinds.SORT_DOWN if self.state['sort_desc'] else kinds.SORT_UP}" if on else ""
            text.set_label(label + mark)
            button.set_css_classes(["column", "on"] if on else ["column"])
            arrow = "descending" if self.state["sort_desc"] else "ascending"
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f"Sort by {label.lower()}" + (f", now {arrow}" if on else "")])

    def _sync_tools(self):
        hidden = self.state["show_hidden"]
        self.btn_hidden.get_child().animate_to(1.0 if hidden else 0.0)
        tip = "Hide hidden files (Ctrl+H)" if hidden else "Show hidden files (Ctrl+H)"
        self.btn_hidden.set_tooltip_text(tip)
        self.btn_hidden.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        self._set_on(self.btn_hidden, hidden)
        view = self.state["view"]
        self._set_on(self.btn_list, view == "list")
        self._set_on(self.btn_grid, view == "grid")
        self.lookup_action("back").set_enabled(bool(self.back_stack))
        self.lookup_action("forward").set_enabled(bool(self.forward_stack))
        self.lookup_action("up").set_enabled(self.cwd is not None and (self.cwd.get_parent() is not None or self.in_trash))

    @staticmethod
    def _set_on(button, on):
        (button.add_css_class if on else button.remove_css_class)("on")

    # ---- header, sidebar, status ----

    def _display_name(self, file):
        if file.get_uri_scheme() == "trash":
            return "Trash"
        path = file.get_path()
        if path == HOME:
            return "Home"
        if path == "/":
            return "System"
        return file.get_basename() or file.get_uri()

    def _render_header(self):
        self.title.set_label(self._display_name(self.cwd))
        while (child := self.crumbs.get_first_child()) is not None:
            self.crumbs.remove(child)
        trail = []
        parent = self.cwd.get_parent()
        while parent is not None:
            trail.append(parent)
            if parent.get_path() == HOME:
                break
            parent = parent.get_parent()
        trail.reverse()
        if not trail:
            self.crumbs.append(Gtk.Label(label=" ", css_classes=["crumb-sep"]))  # keeps the title from jumping
        shown = trail[-6:]
        if len(trail) > len(shown):
            self.crumbs.append(Gtk.Label(label="… /", css_classes=["crumb-sep"]))
        for i, file in enumerate(shown):
            label = "~" if file.get_path() == HOME else "/" if file.get_path() == "/" else self._display_name(file)
            button = Gtk.Button(label=label, css_classes=["crumb"], tooltip_text=file.get_parse_name())
            button.connect("clicked", lambda _b, f=file: self.navigate(f))
            button.add_controller(self._drop_target(lambda f=file: f))
            self.crumbs.append(button)
            if label != "/" and i < len(shown):
                self.crumbs.append(Gtk.Label(label="/", css_classes=["crumb-sep"]))

    def _side_head(self, text):
        self.sidebar.append(Gtk.Label(label=text, xalign=0, css_classes=["side-head"]))

    def _side_row(self, glyph, name, file, tooltip=None, on_click=None):
        button = Gtk.Button(css_classes=["side-row"], tooltip_text=tooltip or (file.get_parse_name() if file else name))
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Label(label=glyph, css_classes=["side-glyph"], xalign=0))
        box.append(Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
        button.set_child(box)
        if file is not None:
            if self.cwd is not None and file.equal(self.cwd):
                button.add_css_class("current")
            button.connect("clicked", lambda *_: self.navigate(file))
            button.add_controller(self._drop_target(lambda: file))
        else:
            button.connect("clicked", lambda *_: on_click())
        return button

    def _places(self):
        """Home, the standard folders that exist, then the bookmarks Nautilus and GTK dialogs also use."""
        places = [(kinds.HOME, "Home", HOME)]
        specials = [
            (GLib.UserDirectory.DIRECTORY_DOWNLOAD, kinds.DOWNLOAD),
            (GLib.UserDirectory.DIRECTORY_DOCUMENTS, kinds.TEXT),
            (GLib.UserDirectory.DIRECTORY_PICTURES, kinds.IMAGE),
            (GLib.UserDirectory.DIRECTORY_MUSIC, kinds.AUDIO),
            (GLib.UserDirectory.DIRECTORY_VIDEOS, kinds.VIDEO),
        ]
        seen = {HOME}
        for special, glyph in specials:
            path = GLib.get_user_special_dir(special)
            if path and path not in seen and os.path.isdir(path):
                seen.add(path)
                places.append((glyph, os.path.basename(path), path))
        for uri, name in read_bookmarks():
            file = Gio.File.new_for_uri(uri)
            path = file.get_path()
            if path in seen or (path and not os.path.isdir(path)):
                continue
            seen.add(path or uri)
            places.append((kinds.BOOKMARK, name or file.get_basename() or uri, path or uri))
        return places, seen

    def _rebuild_sidebar(self):
        while (child := self.sidebar.get_first_child()) is not None:
            self.sidebar.remove(child)

        places, seen = self._places()
        self._side_head("Places")
        for glyph, name, target in places:
            file = Gio.File.new_for_uri(target) if jump.URI.match(target) else Gio.File.new_for_path(target)
            self.sidebar.append(self._side_row(glyph, name, file))

        often = [p for _, p in self.entries if p not in seen and p != "/"][:6]
        if often:
            self._side_head("Often")
            for path in often:
                self.sidebar.append(self._side_row(kinds.RECENT, os.path.basename(path), Gio.File.new_for_path(path),
                                                   tooltip=kinds.home_short(path)))

        self._side_head("Drives")
        self._drive_row(kinds.DRIVE, "System", Gio.File.new_for_path("/"))
        monitor = self.volume_monitor if hasattr(self, "volume_monitor") else Gio.VolumeMonitor.get()
        for mount in monitor.get_mounts():
            if not mount.is_shadowed():
                root = mount.get_root()
                glyph = kinds.USB if mount.can_eject() else kinds.DRIVE
                self._drive_row(glyph, mount.get_name(), root, mount)
        for volume in monitor.get_volumes():
            if volume.get_mount() is None:
                self.sidebar.append(self._side_row(kinds.DRIVE, volume.get_name(), None, on_click=lambda v=volume: self._mount(v)))

        self.sidebar.append(Gtk.Box(vexpand=True))
        self._side_head("")
        self.sidebar.append(self._side_row(kinds.TRASH, "Trash", Gio.File.new_for_uri(TRASH_URI), tooltip="Trash"))

    def _drive_row(self, glyph, name, root, mount=None):
        row = self._side_row(glyph, name, root)
        if mount is not None and (mount.can_eject() or mount.can_unmount()):
            line = Gtk.Box()
            row.set_hexpand(True)
            line.append(row)
            eject = Gtk.Button(label=kinds.EJECT, css_classes=["side-eject"], tooltip_text=f"Eject {name}")
            eject.update_property([Gtk.AccessibleProperty.LABEL], [f"Eject {name}"])
            eject.connect("clicked", lambda *_: self._eject(mount))
            line.append(eject)
            self.sidebar.append(line)
        else:
            self.sidebar.append(row)
        bar = Gtk.ProgressBar(css_classes=["side-use"], visible=False, can_target=False)
        self.sidebar.append(bar)
        root.query_filesystem_info_async("filesystem::size,filesystem::free", GLib.PRIORITY_LOW, None,
                                         self._on_drive_usage, bar)

    @staticmethod
    def _on_drive_usage(root, result, bar):
        try:
            info = root.query_filesystem_info_finish(result)
        except GLib.Error:
            return
        size = info.get_attribute_uint64("filesystem::size")
        if size:
            used = 1 - info.get_attribute_uint64("filesystem::free") / size
            bar.set_fraction(used)
            bar.set_visible(True)
            bar.set_tooltip_text(f"{used:.0%} used")
            if used > 0.9:
                bar.add_css_class("full")

    def _mount(self, volume):
        def done(source, result, _data):
            try:
                source.mount_finish(result)
            except GLib.Error as e:
                self.show_toast(f"Couldn't mount {volume.get_name()}. {e.message}")
                return
            mount = source.get_mount()
            if mount:
                self.navigate(mount.get_root())

        volume.mount(Gio.MountMountFlags.NONE, Gtk.MountOperation.new(self), None, done, None)

    def _eject(self, mount):
        root = mount.get_root()
        if self.cwd and (self.cwd.equal(root) or self.cwd.has_prefix(root)):
            self.navigate(Gio.File.new_for_path(HOME))

        def done(source, result, _data):
            try:
                if eject:
                    source.eject_with_operation_finish(result)
                else:
                    source.unmount_with_operation_finish(result)
                self.show_toast(f"Ejected {mount.get_name()}. It's safe to remove.")
            except GLib.Error as e:
                self.show_toast(f"Couldn't eject {mount.get_name()}. {e.message}")

        eject = mount.can_eject()
        op = Gtk.MountOperation.new(self)
        if eject:
            mount.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done, None)
        else:
            mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, op, None, done, None)

    def _update_status(self):
        if self.dirlist.is_loading() and self.sorted.get_n_items() == 0:
            self.status_left.set_label("Loading…")
            return
        count = self.selection.get_selection().get_size()
        total = self.sorted.get_n_items()
        if count == 0:
            hidden = self.dirlist.get_n_items() - self.filtered.get_n_items()
            text = kinds.plural(total, "item")
            if hidden > 0:
                text += f", {hidden} hidden"
        elif count == 1:
            info = self.selected_infos()[0]
            text = info.get_display_name()
            if info.get_file_type() != Gio.FileType.DIRECTORY:
                text += f", {kinds.size_text(info.get_size())}"
            if info.get_is_symlink() and info.get_symlink_target():
                text += f", links to {info.get_symlink_target()}"
        else:
            text = f"{count} selected"
            if count <= SUMMARY_LIMIT:
                size = sum(i.get_size() for i in self.selected_infos() if i.get_file_type() != Gio.FileType.DIRECTORY)
                text += f", {kinds.size_text(size)}"
        self.status_left.set_label(text)

    def _update_free_space(self):
        self.status_right.set_label("")
        if self.cwd is None or self.in_trash:
            return
        wanted = self.cwd

        def done(source, result, _data):
            try:
                info = source.query_filesystem_info_finish(result)
            except GLib.Error:
                return
            if wanted is self.cwd and info.has_attribute("filesystem::free"):
                self.status_right.set_label(f"{kinds.size_text(info.get_attribute_uint64('filesystem::free'))} free")

        self.cwd.query_filesystem_info_async("filesystem::free", GLib.PRIORITY_LOW, None, done, None)

    # ---- cells ----

    def _attach_cell(self, cell, li):
        cell.li = li
        li.set_child(cell)
        source = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        source.connect("prepare", self._drag_prepare, cell)
        source.connect("drag-begin", self._drag_begin, cell)
        cell.add_controller(source)
        cell.add_controller(self._drop_target(lambda: self.file_of(cell.info) if cell.info else None,
                                              only_dirs=lambda: cell.info is not None
                                              and cell.info.get_file_type() == Gio.FileType.DIRECTORY))

    def _row_setup(self, _factory, li):
        cell = Cell(css_classes=["row"], spacing=12)
        cell.glyph = glyph_label("")
        cell.name = Gtk.Label(xalign=0, css_classes=["name"], ellipsize=Pango.EllipsizeMode.MIDDLE)
        cell.note = Gtk.Label(xalign=0, css_classes=["note"], ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        cell.size = Gtk.Label(xalign=1, css_classes=["size"], width_chars=9)
        cell.when = Gtk.Label(xalign=1, css_classes=["when"], width_chars=8)
        names = Gtk.Box(spacing=10, hexpand=True)
        names.append(cell.glyph)
        names.append(cell.name)
        names.append(cell.note)
        cell.append(names)
        cell.append(cell.size)
        cell.append(cell.when)
        self._attach_cell(cell, li)

    def _bind_common(self, cell, li):
        info = li.get_item()
        cell.info = info
        name = info.get_display_name()
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        mime = info.get_attribute_string("standard::fast-content-type")
        glyph, kind = kinds.kind_of(name, mime, is_dir)
        cell.glyph.set_label(glyph)
        cell.glyph.set_css_classes(["glyph", kind])
        cell.name.set_label(name)
        (cell.add_css_class if is_dir else cell.remove_css_class)("is-dir")
        (cell.add_css_class if info.get_is_hidden() or info.get_is_backup() else cell.remove_css_class)("is-hidden")
        return info, name, is_dir, mime

    def _row_bind(self, _factory, li):
        cell = li.get_child()
        info, name, is_dir, _mime = self._bind_common(cell, li)
        note = ""
        if info.get_is_symlink() and info.get_symlink_target():
            note = f"{kinds.LINK} {info.get_symlink_target()}"
        elif self.in_trash:
            orig = info.get_attribute_byte_string("trash::orig-path")
            note = f"from {kinds.home_short(os.path.dirname(orig))}" if orig else ""
        cell.note.set_label(note)
        cell.size.set_label("" if is_dir else kinds.size_text(info.get_size()))
        cell.when.set_label(kinds.when_text(info.get_attribute_uint64("time::modified")))

    def _grid_setup(self, _factory, li):
        cell = Cell(css_classes=["cell"], orientation=Gtk.Orientation.VERTICAL)
        cell.glyph = glyph_label("")
        cell.picture = Gtk.Picture(css_classes=["thumb"], content_fit=Gtk.ContentFit.COVER, visible=False,
                                   can_shrink=True)
        cell.picture.set_size_request(128, 96)
        cell.name = Gtk.Label(css_classes=["name"], wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                              ellipsize=Pango.EllipsizeMode.MIDDLE, justify=Gtk.Justification.CENTER,
                              max_width_chars=16, width_chars=16)
        cell.append(cell.glyph)
        cell.append(cell.picture)
        cell.append(cell.name)
        self._attach_cell(cell, li)

    def _grid_bind(self, _factory, li):
        cell = li.get_child()
        info, _name, is_dir, mime = self._bind_common(cell, li)
        cell.glyph.set_visible(True)
        cell.picture.set_visible(False)
        if is_dir or not kinds.thumbable(mime):
            return
        file = self.file_of(info)
        path = file.get_path()
        if not path or file.get_uri_scheme() == "trash":
            return

        def show(texture):
            if cell.info is info:
                cell.picture.set_paintable(texture)
                cell.picture.set_visible(True)
                cell.glyph.set_visible(False)

        cell.token = shared_thumbs.request(path, file.get_uri(), info.get_attribute_uint64("time::modified"), mime, show)

    def _cell_unbind(self, _factory, li):
        cell = li.get_child()
        cell.info = None
        if cell.token is not None:
            shared_thumbs.cancel(cell.token)
            cell.token = None

    def _visible_cell(self, position):
        """The on-screen widget for a position, or None if it's scrolled out of view."""
        def walk(widget):
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, Cell) and child.li is not None and child.li.get_position() == position:
                    return child
                found = walk(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        return walk(self._view())

    # ---- opening ----

    def _on_activate(self, _view, position):
        info = self.sorted.get_item(position)
        selected = self.selected_infos()
        targets = selected if info in selected and len(selected) > 1 else [info]
        self._open_infos(targets)

    def open_selection(self):
        self._open_infos(self.selected_infos())

    def _open_infos(self, infos):
        dirs = [i for i in infos if i.get_file_type() == Gio.FileType.DIRECTORY]
        files = [i for i in infos if i.get_file_type() != Gio.FileType.DIRECTORY]
        for info in files:
            self.open_file(self.file_of(info), info.get_display_name())
        if len(dirs) == 1:
            self.navigate(self.file_of(dirs[0]))
        else:
            for info in dirs:
                self.get_application().new_window(self.file_of(info))

    def open_file(self, file, name):
        def done(_source, result, _data):
            try:
                Gio.AppInfo.launch_default_for_uri_finish(result)
            except GLib.Error:
                self.show_toast(f"No app is set to open {name}. Right-click it and pick one from Open with.")

        context = self.get_display().get_app_launch_context()
        Gio.AppInfo.launch_default_for_uri_async(file.get_uri(), context, None, done, None)

    def _open_with(self, param):
        app = Gio.DesktopAppInfo.new(param.get_string())
        if app is None:
            return
        try:
            app.launch(self.selected_files(), self.get_display().get_app_launch_context())
        except GLib.Error as e:
            self.show_toast(f"Couldn't open with {app.get_name()}. {e.message}")

    def open_terminal(self):
        path = self.cwd.get_path() or HOME
        infos = self.selected_infos()
        if len(infos) == 1 and infos[0].get_file_type() == Gio.FileType.DIRECTORY and self.file_of(infos[0]).get_path():
            path = self.file_of(infos[0]).get_path()
        if self.in_trash:
            path = HOME
        try:
            subprocess.Popen(["xdg-terminal-exec", f"--dir={path}"], cwd=path, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.show_toast(f"Couldn't open a terminal. {e.strerror}")

    def copy_path(self):
        files = self.selected_files() or [self.cwd]
        text = "\n".join(f.get_path() or f.get_uri() for f in files)
        self.get_clipboard().set(text)
        self.show_toast("Copied path" if len(files) == 1 else f"Copied {len(files)} paths")

    def toggle_bookmark(self):
        infos = self.selected_infos()
        target = self.file_of(infos[0]) if len(infos) == 1 and infos[0].get_file_type() == Gio.FileType.DIRECTORY else self.cwd
        uri = target.get_uri()
        marks = read_bookmarks()
        name = self._display_name(target)
        if any(u == uri for u, _ in marks):
            marks = [(u, n) for u, n in marks if u != uri]
            self.show_toast(f"Removed {name} from the sidebar")
        else:
            marks.append((uri, None))
            self.show_toast(f"Added {name} to the sidebar")
        write_bookmarks(marks)
        self._rebuild_sidebar()

    # ---- clipboard and jobs ----

    def clip_selection(self, cut):
        files = self.selected_files()
        if not files:
            return
        uris = "\n".join(f.get_uri() for f in files)
        gnome = GLib.Bytes.new(f"{'cut' if cut else 'copy'}\n{uris}".encode())
        uri_list = GLib.Bytes.new((uris.replace("\n", "\r\n") + "\r\n").encode())
        plain = GLib.Bytes.new("\n".join(f.get_path() or f.get_uri() for f in files).encode())
        self.get_clipboard().set_content(Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_bytes("x-special/gnome-copied-files", gnome),
            Gdk.ContentProvider.new_for_bytes("text/uri-list", uri_list),
            Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", plain),
        ]))
        verb = "Cut" if cut else "Copied"
        self.show_toast(f"{verb} {kinds.plural(len(files), 'item')}")

    def paste(self):
        if self.in_trash:
            return
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        mime = next((m for m in ("x-special/gnome-copied-files", "text/uri-list") if formats.contain_mime_type(m)), None)
        if mime is None:
            self.show_toast("Nothing to paste. Copy some files first.")
            return

        def read_done(source, result, _data):
            try:
                stream, used = source.read_finish(result)
            except GLib.Error as e:
                self.show_toast(f"Couldn't paste. {e.message}")
                return

            def spliced(out, spliced_result, _data):
                try:
                    out.splice_finish(spliced_result)
                except GLib.Error as e:
                    self.show_toast(f"Couldn't paste. {e.message}")
                    return
                text = out.steal_as_bytes().get_data().decode("utf-8", "replace")
                cut, uris = ops.parse_clipboard(used, text)
                if uris:
                    self.start_job("move" if cut else "copy", [Gio.File.new_for_uri(u) for u in uris], self.cwd)
                    if cut:
                        clipboard.set_content(None)

            # Must stay async: when the copy came from this app, the main loop is what fills this stream.
            Gio.MemoryOutputStream.new_resizable().splice_async(
                stream, Gio.OutputStreamSpliceFlags.CLOSE_SOURCE | Gio.OutputStreamSpliceFlags.CLOSE_TARGET,
                GLib.PRIORITY_DEFAULT, None, spliced, None)

        clipboard.read_async([mime], GLib.PRIORITY_DEFAULT, None, read_done, None)

    def start_job(self, kind, sources, dest=None, **extra):
        job = ops.Job(kind, sources, dest, on_update=self._job_update, on_conflict=self._job_conflict,
                      on_done=self._job_done)
        job.extra = extra
        job.dest_at_start = self.cwd
        box = Gtk.Box(css_classes=["job"], spacing=12)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        bar = Gtk.ProgressBar()
        column.append(label)
        column.append(bar)
        stop = tool_button(icons.cancel, "Cancel")
        stop.connect("clicked", lambda *_: job.cancel())
        box.append(column)
        box.append(stop)
        box.set_visible(False)
        self.jobs_box.append(box)
        self.jobs[job] = (box, label, bar)
        # Fast operations finish without a progress bar flashing up.
        GLib.timeout_add(350, lambda: self._reveal_job(job))
        job.start()
        return job

    def _reveal_job(self, job):
        if job in self.jobs:
            self.jobs[job][0].set_visible(True)
            self._job_update(job)
        return False

    VERBS = {"copy": "Copying", "move": "Moving", "trash": "Moving to Trash", "delete": "Deleting",
             "restore": "Restoring", "undo-trash": "Restoring", "empty-trash": "Emptying Trash"}

    def _job_update(self, job):
        if job not in self.jobs:
            return False
        _box, label, bar = self.jobs[job]
        target = f" to {self._display_name(job.dest)}" if job.dest else ""
        label.set_label(f"{self.VERBS[job.kind]} {kinds.plural(job.total_items, 'item')}{target}")
        bar.set_fraction(job.fraction)
        return False

    DONE = {"copy": "Copied", "move": "Moved", "restore": "Restored", "undo-trash": "Restored", "delete": "Deleted"}

    def _job_done(self, job):
        box = self.jobs.pop(job)[0]
        self.jobs_box.remove(box)

        if job.untrashable:
            self._ask_delete_untrashable(job.untrashable)
        if job.errors:
            lines = [f"{name}: {message}" if name else message for name, message in job.errors[:6]]
            if len(job.errors) > 6:
                lines.append(f"…and {len(job.errors) - 6} more")
            self.ask("Some items couldn't be finished", "\n".join(lines), [("Close", "close", "primary")], lambda *_: None)
        elif job.cancelled:
            self.show_toast(f"Stopped. {kinds.plural(job.count, 'item')} finished before that.")
        elif job.kind == "trash" and job.trashed:
            self.show_toast(f"Moved {kinds.plural(len(job.trashed), 'item')} to Trash", "Undo",
                            lambda: self.start_job("undo-trash", job.trashed))
        elif job.kind == "empty-trash":
            self.show_toast("Emptied Trash")
        elif job.kind in self.DONE and job.count:
            self.show_toast(f"{self.DONE[job.kind]} {kinds.plural(job.count, 'item')}")
        if job.done_names and job.dest and self.cwd and job.dest.equal(self.cwd):
            self.select_names(job.done_names)
        return False

    def _job_conflict(self, job, src, dst, conflict):
        name = dst.get_basename()
        where = self._display_name(dst.get_parent())
        if conflict == "folders":
            title, body = f"A folder named {name} is already in {where}", "Merging keeps both folders' files together. You'll be asked about any file that clashes."
            buttons = [("Stop", ops.STOP, ""), ("Skip", ops.SKIP, ""), ("Keep both", ops.KEEP_BOTH, ""), ("Merge", ops.MERGE, "primary")]
        elif conflict == "files":
            title, body = f"A file named {name} is already in {where}", self._compare_text(src, dst)
            buttons = [("Stop", ops.STOP, ""), ("Skip", ops.SKIP, ""), ("Keep both", ops.KEEP_BOTH, "primary"), ("Replace", ops.REPLACE, "danger")]
        else:
            title, body = f"Something named {name} is already in {where}", "One is a file and the other is a folder, so they can't be merged or replaced."
            buttons = [("Stop", ops.STOP, ""), ("Skip", ops.SKIP, ""), ("Keep both", ops.KEEP_BOTH, "primary")]

        def answered(choice, apply_all):
            job.answer(choice, apply_all and choice != ops.STOP, conflict)

        self.ask(title, body, buttons, answered, offer_all=True, default=ops.STOP)
        return False

    @staticmethod
    def _compare_text(src, dst):
        try:
            a = src.query_info("standard::size,time::modified", ops.NOFOLLOW, None)
            b = dst.query_info("standard::size,time::modified", ops.NOFOLLOW, None)
        except GLib.Error:
            return ""
        def describe(info):
            return f"{kinds.size_text(info.get_size())}, modified {kinds.when_text(info.get_attribute_uint64('time::modified'))}"
        return f"Incoming: {describe(a)}\nExisting: {describe(b)}"

    def trash_selection(self):
        files = self.selected_files()
        if not files:
            return
        if self.in_trash:
            self.delete_selection()
            return
        self.start_job("trash", files)

    def delete_selection(self):
        files = self.selected_files()
        if not files:
            return
        name = self.selected_infos()[0].get_display_name() if len(files) == 1 else kinds.plural(len(files), "item")
        self._confirm_delete(files, f"Delete {name} permanently?", "This can't be undone.")

    def _ask_delete_untrashable(self, files):
        name = files[0].get_basename() if len(files) == 1 else kinds.plural(len(files), "item")
        self._confirm_delete(files, f"Delete {name} permanently?", "This drive has no Trash, so it can't be undone.")

    def _confirm_delete(self, files, title, body):
        def answered(choice, _all):
            if choice == "delete":
                self.start_job("delete", files)
        self.ask(title, body, [("Cancel", "cancel", ""), ("Delete permanently", "delete", "danger")], answered,
                 danger=True, default="cancel")

    def restore_selection(self):
        files = self.selected_files()
        if files:
            self.start_job("restore", files)

    def empty_trash(self):
        def answered(choice, _all):
            if choice == "empty":
                self.start_job("empty-trash", [])
        self.ask("Empty Trash?", "Everything in Trash will be deleted permanently. This can't be undone.",
                 [("Cancel", "cancel", ""), ("Empty Trash", "empty", "danger")], answered, danger=True, default="cancel")

    # ---- context menu ----

    def _on_view_click(self, gesture, _n, x, y, view):
        button = gesture.get_current_button()
        widget = view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not view and not isinstance(widget, Cell):
            widget = widget.get_parent()
        cell = widget if isinstance(widget, Cell) else None

        if button == Gdk.BUTTON_PRIMARY:
            if cell is None:
                self.selection.unselect_all()
                view.grab_focus()
        elif button == Gdk.BUTTON_SECONDARY:
            if cell is not None:
                position = cell.li.get_position()
                if not self.selection.is_selected(position):
                    self.selection.select_item(position, True)
            else:
                self.selection.unselect_all()
            self._show_menu(view, x, y)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _show_menu(self, view, x, y):
        ok, point = view.compute_point(self.view_overlay, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(point.x), int(point.y)
        rect.width = rect.height = 1
        self.menu.set_menu_model(self._build_menu(self.selected_infos()))
        self.menu.set_pointing_to(rect)
        self.menu.popup()

    def _build_menu(self, infos):
        menu = Gio.Menu()

        def section(*items):
            part = Gio.Menu()
            for label, action in items:
                part.append(label, action)
            menu.append_section(None, part)

        if not infos:
            if self.in_trash:
                section(("Empty Trash", "win.empty-trash"))
                return menu
            section(("New folder", "win.new-folder"), ("New file", "win.new-file"))
            section(("Paste", "win.paste"), ("Select all", "win.select-all"))
            section(("Open terminal here", "win.terminal"), ("Copy path", "win.copy-path"),
                    ("Add to sidebar", "win.bookmark"))
            section(("Hide hidden files" if self.state["show_hidden"] else "Show hidden files", "win.toggle-hidden"))
            return menu

        if self.in_trash:
            section(("Restore", "win.restore"))
            section(("Delete permanently", "win.delete"))
            return menu

        section(("Open", "win.open"))
        if len(infos) == 1 and infos[0].get_file_type() != Gio.FileType.DIRECTORY:
            mime = (self.file_mime(infos[0]))
            apps = Gio.AppInfo.get_all_for_type(mime) if mime else []
            if apps:
                sub = Gio.Menu()
                for app in apps[:12]:
                    if app.get_id():
                        item = Gio.MenuItem.new(app.get_name(), None)
                        item.set_action_and_target_value("win.open-with", GLib.Variant.new_string(app.get_id()))
                        sub.append_item(item)
                menu.append_submenu("Open with", sub)
        section(("Cut", "win.cut"), ("Copy", "win.copy"), ("Copy path", "win.copy-path"))
        part = [("Rename", "win.rename")] if len(infos) == 1 else []
        section(*part, ("Move to Trash", "win.trash"), ("Delete permanently", "win.delete"))
        if len(infos) == 1 and infos[0].get_file_type() == Gio.FileType.DIRECTORY:
            section(("Open terminal here", "win.terminal"), ("Add to sidebar", "win.bookmark"))
        return menu

    def file_mime(self, info):
        try:
            full = self.file_of(info).query_info("standard::content-type", 0, None)
            return full.get_content_type()
        except GLib.Error:
            return info.get_attribute_string("standard::fast-content-type")

    # ---- naming ----

    def _name_popover(self, position, initial, select_stem, on_submit):
        cell = self._visible_cell(position) if position is not None else None
        popover = Gtk.Popover(css_classes=["rename"], has_arrow=False, position=Gtk.PositionType.BOTTOM)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        entry = Gtk.Entry(text=initial, activates_default=False)
        error = Gtk.Label(css_classes=["rename-error"], xalign=0, visible=False, wrap=True, max_width_chars=44)
        box.append(entry)
        box.append(error)
        popover.set_child(box)
        popover.set_parent(self.view_overlay)

        rect = Gdk.Rectangle()
        if cell is not None:
            ok, bounds = cell.compute_bounds(self.view_overlay)
            rect.x, rect.y = int(bounds.get_x()) + 30, int(bounds.get_y())
            rect.width, rect.height = 1, int(bounds.get_height())
        else:
            rect.x, rect.y = 40, 20
            rect.width = rect.height = 1
        popover.set_pointing_to(rect)

        def submit(*_):
            text = entry.get_text().strip()
            problem = None
            if not text:
                problem = "Enter a name."
            elif "/" in text:
                problem = "Names can't contain /."
            elif text in (".", ".."):
                problem = "That name is reserved."
            else:
                problem = on_submit(text)
            if problem:
                error.set_label(problem)
                error.set_visible(True)
            else:
                popover.popdown()

        def closed(*_):
            GLib.idle_add(popover.unparent)
            self._view().grab_focus()

        entry.connect("activate", submit)
        popover.connect("closed", closed)
        popover.popup()
        entry.grab_focus()
        stem = len(ops.split_name(initial)[0]) if select_stem else len(initial)
        entry.select_region(0, stem)
        return popover

    def rename_selection(self):
        infos = self.selected_infos()
        if len(infos) != 1 or self.in_trash:
            return
        info = infos[0]
        file = self.file_of(info)
        name = info.get_display_name()
        position = self.selection.get_selection().get_nth(0)

        def submit(new):
            if new == name:
                return None
            try:
                renamed = file.set_display_name(new, None)  # refuses to replace an existing file
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                    return f"{new} already exists here."
                return e.message
            self.select_names([renamed.get_basename()])
            return None

        self._name_popover(position, name, info.get_file_type() != Gio.FileType.DIRECTORY, submit)

    def prompt_new(self, folder):
        if self.in_trash:
            return

        def submit(name):
            child = self.cwd.get_child_for_display_name(name)
            try:
                if folder:
                    child.make_directory(None)
                else:
                    child.create(Gio.FileCreateFlags.NONE, None).close(None)
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                    return f"{name} already exists here."
                return e.message
            self.select_names([child.get_basename()])
            return None

        self._name_popover(None, "New folder" if folder else "New file", False, submit)

    # ---- drag and drop ----

    def _drag_prepare(self, _source, _x, _y, cell):
        if cell.info is None:
            return None
        position = cell.li.get_position()
        if not self.selection.is_selected(position):
            self.selection.select_item(position, True)
        files = Gdk.FileList.new_from_array(self.selected_files())
        return Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, files))

    def _drag_begin(self, source, _drag, cell):
        source.set_icon(Gtk.WidgetPaintable.new(cell.glyph), 0, 0)

    def _drop_target(self, get_dest, only_dirs=None):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)

        def preferred(*_):
            if only_dirs is not None and not only_dirs():
                return 0  # lets the folder view behind this row take the drop
            drop = target.get_current_drop()
            actions = drop.get_actions() if drop else Gdk.DragAction.COPY
            return Gdk.DragAction.MOVE if actions & Gdk.DragAction.MOVE else Gdk.DragAction.COPY

        def dropped(_target, value, _x, _y):
            dest = get_dest()
            if dest is None:
                return False
            drop = target.get_current_drop()
            allowed = drop.get_actions() if drop else Gdk.DragAction.COPY
            self.handle_drop(value.get_files(), dest, target.get_current_event_state(), allowed)
            return True

        target.connect("enter", preferred)
        target.connect("motion", preferred)
        target.connect("drop", dropped)
        return target

    def handle_drop(self, files, dest, modifiers, allowed):
        if not files:
            return
        if dest.get_uri_scheme() == "trash":
            self.start_job("trash", files)
            return
        if not allowed & Gdk.DragAction.MOVE:
            move = False  # the source only offered a copy, so its files must stay
        elif modifiers & Gdk.ModifierType.CONTROL_MASK:
            move = False
        elif modifiers & Gdk.ModifierType.SHIFT_MASK:
            move = True
        else:
            move = same_filesystem(files[0], dest)  # same rule as other file managers
        self.start_job("move" if move else "copy", files, dest)

    # ---- quick search ----

    def _on_key(self, controller, keyval, _code, modifiers):
        if self.sheet_open:
            if keyval == Gdk.KEY_Escape:
                self._sheet_answer(self.sheet_default)
                return True
            return False
        if self.jump_open:
            if keyval == Gdk.KEY_Escape:
                self.close_jump()
            elif keyval == Gdk.KEY_Down or (keyval == Gdk.KEY_n and modifiers & Gdk.ModifierType.CONTROL_MASK):
                self._jump_move(1)
            elif keyval == Gdk.KEY_Up or (keyval == Gdk.KEY_p and modifiers & Gdk.ModifierType.CONTROL_MASK):
                self._jump_move(-1)
            elif keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
                self._jump_complete()
            else:
                # Window accels run before a focused entry, and would eat Backspace, Delete and Ctrl+A.
                return controller.forward(self.jump_entry)
            return True
        if modifiers & MOD_MASK or self._text_has_focus():
            return False
        if keyval == Gdk.KEY_Escape:
            self.selection.unselect_all()
            return False
        code = Gdk.keyval_to_unicode(keyval)
        if code and chr(code).isprintable() and not chr(code).isspace():
            self.open_jump(chr(code))
            return True
        return False

    def _text_has_focus(self):
        focus = self.get_focus()
        if isinstance(focus, Gtk.Text) or isinstance(focus, Gtk.Editable):
            return True
        return self.menu.get_visible() or (focus is not None and focus.get_ancestor(Gtk.Popover) is not None)

    def _on_mouse_nav(self, gesture, *_):
        button = gesture.get_current_button()
        if button == 8:
            self.activate_action("win.back", None)
        elif button == 9:
            self.activate_action("win.forward", None)

    def _here_names(self):
        return [i.get_display_name() for i in self.items()[:20000]]

    def open_jump(self, text=""):
        if self.jump_open:
            return
        self.jump_open = True
        self._hide_toast()
        self.here_names = self._here_names()
        self.content.add_css_class("behind")
        self.jump.set_visible(True)
        self.jump_entry.set_text(text)
        self._on_jump_changed()
        self.jump_entry.grab_focus()
        self.jump_entry.set_position(-1)
        self._refresh_entries()

    def _open_location(self):
        path = self.cwd.get_parse_name()
        if self.cwd.get_path():
            path = kinds.home_short(self.cwd.get_path()).rstrip("/") + "/"
        self.open_jump(path)

    def close_jump(self):
        self.jump_open = False
        self.jump.set_visible(False)
        self.content.remove_css_class("behind")
        self._view().grab_focus()

    def _on_jump_backdrop(self, gesture, _n, x, y):
        widget = self.jump.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not self.jump:
            if widget is self.jump_entry or "hit" in widget.get_css_classes():
                return
            widget = widget.get_parent()
        if widget is self.jump:
            self.close_jump()

    def _on_jump_changed(self):
        query = self.jump_entry.get_text()
        if not self.jump_open:
            return
        cwd_path = self.cwd.get_path() or HOME
        location = jump.is_location(query)
        if location:
            self.jump_hits = [jump.Hit("path", p, 0.0) for p in jump.complete(query, cwd_path, self.state["show_hidden"])]
        else:
            entries = [e for e in self.entries if e[1] != cwd_path]  # `z` skips the folder you're in too
            self.jump_hits = jump.search(query, entries, self.here_names)
        self.jump_index = 0
        self._render_hits(query.strip(), location)

    def _render_hits(self, query, location):
        while (child := self.jump_list.get_first_child()) is not None:
            self.jump_list.remove(child)
        if not self.jump_hits:
            if location:
                text = "Press Enter to go to this path." if query else ""
            else:
                text = "No folder or file matches. Keep in mind only folders you've visited are searched."
            self.jump_list.append(Gtk.Label(label=text, xalign=0, css_classes=["jump-empty"]))
            return
        top = math.log1p(self.entries[0][0]) if self.entries else 1
        for i, hit in enumerate(self.jump_hits):
            self.jump_list.append(self._hit_row(i, hit, query, top))
        self._mark_current()

    def _hit_row(self, index, hit, query, top):
        needle = query.split()[-1] if query and hit.kind != "path" else ""
        name = hit.path if hit.kind == "here" else (os.path.basename(hit.path.rstrip("/")) or hit.path)
        marks = set(jump.positions(needle, name) or [])
        pieces = []
        for at, ch in enumerate(name):
            text = GLib.markup_escape_text(ch)
            pieces.append(f'<span underline="single">{text}</span>' if at in marks else text)
        markup = "".join(pieces)
        if hit.kind == "jump":
            # Frequently used folders are physically bigger: the terminal's ranking, made visible.
            px = 15 + 21 * (math.log1p(hit.score) / top if top else 0)
            weight = "bold" if px > 22 else "normal"
        else:
            px, weight = 15, "normal"
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, valign=Gtk.Align.BASELINE)
        label.set_markup(f'<span size="{int(px * 0.75 * Pango.SCALE)}" weight="{weight}">{markup}</span>')
        row = Gtk.Box(spacing=12)
        row.append(label)
        if hit.kind == "here":
            detail = "in this folder"
        else:
            detail = kinds.home_short(os.path.dirname(hit.path.rstrip("/")))
        row.append(Gtk.Label(label=detail, xalign=0, css_classes=["hit-path"], valign=Gtk.Align.BASELINE,
                             ellipsize=Pango.EllipsizeMode.START))
        button = Gtk.Button(child=row, css_classes=["hit"], can_focus=False)
        button.connect("clicked", lambda *_: self._jump_pick(index))
        return button

    def _mark_current(self):
        child = self.jump_list.get_first_child()
        i = 0
        while child is not None:
            (child.add_css_class if i == self.jump_index else child.remove_css_class)("current")
            child = child.get_next_sibling()
            i += 1

    def _jump_move(self, delta):
        if self.jump_hits:
            self.jump_index = (self.jump_index + delta) % len(self.jump_hits)
            self._mark_current()

    def _jump_complete(self):
        if self.jump_hits and self.jump_hits[self.jump_index].kind != "here":
            self.jump_entry.set_text(kinds.home_short(self.jump_hits[self.jump_index].path) + "/")
            self.jump_entry.set_position(-1)

    def _jump_pick(self, index):
        self.jump_index = index
        self._jump_accept()

    def _jump_accept(self):
        query = self.jump_entry.get_text().strip()
        hit = self.jump_hits[self.jump_index] if self.jump_hits else None
        if hit is None and not jump.is_location(query):
            return
        self.close_jump()
        if hit is None:
            self.open_location(query)
        elif hit.kind == "here":
            info = next((i for i in self.items() if i.get_display_name() == hit.path), None)
            if info is not None:
                self._open_infos([info])
                self.select_names([info.get_name()])
        else:
            self.navigate(Gio.File.new_for_path(hit.path))

    # ---- sheet and toast ----

    def ask(self, title, body, buttons, callback, offer_all=False, danger=False, default=None):
        """Shows a modal question. `buttons` is [(label, value, style)]. Esc gives `default`."""
        request = (title, body, buttons, callback, offer_all, danger, default)
        if self.sheet_open:
            self.sheet_queue.append(request)
        else:
            self._show_sheet(*request)

    def _show_sheet(self, title, body, buttons, callback, offer_all, danger, default):
        self.sheet_open = True
        self.sheet_callback = callback
        self.sheet_default = default if default is not None else buttons[0][1]
        self.sheet_title.set_label(title)
        self.sheet_body.set_label(body)
        self.sheet_body.set_visible(bool(body))
        self.sheet_check.set_visible(offer_all)
        self.sheet_check.set_active(False)
        (self.sheet.add_css_class if danger else self.sheet.remove_css_class)("danger")
        while (child := self.sheet_actions.get_first_child()) is not None:
            self.sheet_actions.remove(child)
        focus = None
        for label, value, style in buttons:
            button = Gtk.Button(label=label, css_classes=["act"] + ([style] if style else []))
            button.connect("clicked", lambda *_, v=value: self._sheet_answer(v))
            self.sheet_actions.append(button)
            if value == self.sheet_default or focus is None:
                focus = button
        self.content.add_css_class("behind")
        self.scrim.set_visible(True)
        focus.grab_focus()

    def _sheet_answer(self, value):
        apply_all = self.sheet_check.get_active()
        callback = self.sheet_callback
        self.sheet_open = False
        self.scrim.set_visible(False)
        if not self.jump_open:
            self.content.remove_css_class("behind")
        if self.sheet_queue:
            self._show_sheet(*self.sheet_queue.pop(0))
        else:
            self._view().grab_focus()
        callback(value, apply_all)

    def show_toast(self, text, action_label=None, on_action=None):
        self.toast_label.set_label(text)
        self.toast_action.set_visible(bool(action_label))
        self.toast_action.set_label(action_label or "")
        self.toast_handler = on_action
        self.toast.set_reveal_child(True)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)
        self.toast_timer = GLib.timeout_add_seconds(6, self._on_toast_timeout)

    def _hide_toast(self):
        self.toast.set_reveal_child(False)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)
        self.toast_timer = 0
        return False

    def _on_toast_timeout(self):
        self.toast_timer = 0
        self.toast.set_reveal_child(False)
        return False

    def _on_toast_action(self, *_):
        self._hide_toast()
        if self.toast_handler:
            self.toast_handler()

    def _on_close(self, *_):
        width, height = self.get_width(), self.get_height()
        if width > 200 and height > 200:
            self.state["width"], self.state["height"] = width, height
        state.save(self.state)
        return False


def same_filesystem(a, b):
    try:
        return (a.query_info("id::filesystem", 0, None).get_attribute_string("id::filesystem")
                == b.query_info("id::filesystem", 0, None).get_attribute_string("id::filesystem"))
    except GLib.Error:
        return False


def read_bookmarks():
    """GTK's bookmarks file, shared with Nautilus and every GTK file dialog."""
    marks = []
    try:
        with open(BOOKMARKS, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(" ", 1)
                if parts and parts[0]:
                    marks.append((parts[0], parts[1] if len(parts) > 1 else None))
    except OSError:
        pass
    return marks


def write_bookmarks(marks):
    os.makedirs(os.path.dirname(BOOKMARKS), exist_ok=True)
    tmp = BOOKMARKS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for uri, name in marks:
            f.write(f"{uri} {name}\n" if name else f"{uri}\n")
    os.replace(tmp, BOOKMARKS)
