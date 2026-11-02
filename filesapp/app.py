"""Application: single instance, theme loading and the file-manager D-Bus service."""
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import APP_ID, theme
from .window import FilesWindow

FM1_NAME = "org.freedesktop.FileManager1"
FM1_PATH = "/org/freedesktop/FileManager1"
FM1_XML = f"""
<node><interface name='{FM1_NAME}'>
  <method name='ShowFolders'><arg type='as' direction='in'/><arg type='s' direction='in'/></method>
  <method name='ShowItems'><arg type='as' direction='in'/><arg type='s' direction='in'/></method>
  <method name='ShowItemProperties'><arg type='as' direction='in'/><arg type='s' direction='in'/></method>
</interface></node>
"""


class FilesApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.set_inactivity_timeout(10000)  # stays alive briefly when started just to answer a D-Bus call
        self.css = Gtk.CssProvider()
        self.theme_timer = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.apply_theme()
        self.theme_monitor = Gio.File.new_for_path(theme.THEME_DIR).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.theme_monitor.connect("changed", self._on_theme_changed)
        self._export_file_manager()

    def apply_theme(self):
        palette = theme.load_palette()
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", palette["mode"] != "light")
        self.css.load_from_string(theme.build_css(palette, theme.system_font()))
        self.theme_timer = 0
        return False

    def _on_theme_changed(self, *_):
        if not self.theme_timer:  # omarchy-theme-set swaps several files in quick succession
            self.theme_timer = GLib.timeout_add(250, self.apply_theme)

    def _export_file_manager(self):
        """Answers "show in folder" from browsers and portals, which would otherwise launch Nautilus."""
        connection = self.get_dbus_connection()
        if connection is None:
            return
        node = Gio.DBusNodeInfo.new_for_xml(FM1_XML)
        try:
            connection.register_object(FM1_PATH, node.interfaces[0], self._on_fm1_call, None, None)
            connection.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                GLib.Variant("(su)", (FM1_NAME, 0)), None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        except GLib.Error:
            pass  # another window of this app, or Nautilus, already answers; nothing here depends on it

    def _on_fm1_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        uris = params.unpack()[0]
        if method == "ShowItems":
            by_parent = {}
            for uri in uris:
                file = Gio.File.new_for_uri(uri)
                parent = file.get_parent() or file
                by_parent.setdefault(parent.get_uri(), (parent, []))[1].append(file.get_basename())
            for parent, names in by_parent.values():
                self.new_window(parent, names)
        else:
            for uri in uris:
                self.new_window(Gio.File.new_for_uri(uri))
        invocation.return_value(None)

    def new_window(self, start=None, select=None):
        window = FilesWindow(self, start, select)
        window.present()
        return window

    def do_activate(self):
        self.new_window()

    def do_open(self, files, _count, _hint):
        for file in files:
            info = None
            try:
                info = file.query_info("standard::type", 0, None)
            except GLib.Error:
                pass
            if info is not None and info.get_file_type() != Gio.FileType.DIRECTORY and file.get_parent():
                self.new_window(file.get_parent(), [file.get_basename()])
            else:
                self.new_window(file)


def main():
    return FilesApp().run(sys.argv)
