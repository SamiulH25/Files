"""Toolbar icons, drawn with cairo on an 18x18 grid in the widget's CSS colour.

Not font glyphs: Nerd Font icons are wider than their advance (the eye is 17px of ink on 9px), so a centred label
still draws them 3-4px to the right.
"""
import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

SIZE = 18
STROKE = 1.5
ANIMATION_US = 220_000


def _line(cr, *points):
    cr.move_to(*points[0])
    for point in points[1:]:
        cr.line_to(*point)
    cr.stroke()


def back(cr, _):
    _line(cr, (14.5, 9), (3.5, 9))
    _line(cr, (8, 4.5), (3.5, 9), (8, 13.5))


def forward(cr, _):
    _line(cr, (3.5, 9), (14.5, 9))
    _line(cr, (10, 4.5), (14.5, 9), (10, 13.5))


def up(cr, _):
    _line(cr, (9, 14.5), (9, 3.5))
    _line(cr, (4.5, 8), (9, 3.5), (13.5, 8))


def list_view(cr, _):
    for y in (4.5, 9, 13.5):
        cr.arc(3.2, y, 1, 0, math.tau)
        cr.fill()
        _line(cr, (6.7, y), (15, y))


def grid_view(cr, _):
    for x in (3, 10.2):
        for y in (3, 10.2):
            cr.rectangle(x, y, 4.8, 4.8)
    cr.stroke()


def search(cr, _):
    cr.arc(8, 8, 5, 0, math.tau)
    cr.stroke()
    _line(cr, (11.7, 11.7), (15.2, 15.2))


def cancel(cr, _):
    _line(cr, (5, 5), (13, 13))
    _line(cr, (13, 5), (5, 13))


def eye(cr, opened):
    """`opened` runs from 0 (shut, with lashes) to 1 (open). It lifts as it shuts, because the lashes hang lower."""
    shut = 1 - opened
    cr.translate(0, -3.3 * shut)

    def lids():
        upper = 3.4 + 11.2 * shut
        cr.move_to(1.6, 9)
        cr.curve_to(5.6, upper, 12.4, upper, 16.4, 9)
        cr.curve_to(12.4, 14.6, 5.6, 14.6, 1.6, 9)
        cr.close_path()

    if opened > 0.02:
        red, green, blue, alpha = cr.get_source().get_rgba()
        cr.save()
        lids()
        cr.clip()  # the falling lid covers the iris
        glow = cairo.RadialGradient(9, 9, 1, 9, 9, 7)
        glow.add_color_stop_rgba(0, red, green, blue, alpha * 0.4)
        glow.add_color_stop_rgba(1, red, green, blue, 0)
        cr.set_source(glow)
        cr.paint()
        cr.set_source_rgba(red, green, blue, alpha)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.arc(9, 9, 2.7, 0, math.tau)
        cr.new_sub_path()
        cr.arc(10, 8, 0.8, 0, math.tau)  # catchlight
        cr.fill()
        cr.restore()
    lids()
    cr.stroke()
    if shut > 0.02:
        for (x, y), (dx, dy) in (((5.2, 12.1), (-1.3, 1.9)), ((9, 13.2), (0, 2.4)), ((12.8, 12.1), (1.3, 1.9))):
            _line(cr, (x, y), (x + dx * shut, y + dy * shut))


def paint(cr, painter, value, red=1, green=1, blue=1, alpha=1):
    cr.set_source_rgba(red, green, blue, alpha)
    cr.set_line_width(STROKE)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    painter(cr, value)


class Icon(Gtk.DrawingArea):
    def __init__(self, painter, value=1.0):
        super().__init__(content_width=SIZE, content_height=SIZE, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.painter = painter
        self.value = self.target = value
        self.tick = None
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        cr.translate((width - SIZE) / 2, (height - SIZE) / 2)
        color = self.get_color()
        paint(cr, self.painter, self.value, color.red, color.green, color.blue, color.alpha)

    def animate_to(self, target):
        if target == self.target:
            return
        self.target = target
        self.origin = self.value
        self.started = None
        if self.get_mapped() and self.get_settings().get_property("gtk-enable-animations"):
            if self.tick is None:
                self.tick = self.add_tick_callback(self._on_tick)
            return
        if self.tick is not None:
            self.remove_tick_callback(self.tick)
            self.tick = None
        self.value = target
        self.queue_draw()

    def _on_tick(self, _widget, clock):
        now = clock.get_frame_time()
        self.started = self.started or now
        progress = min((now - self.started) / ANIMATION_US, 1)
        self.value = self.origin + (self.target - self.origin) * (1 - (1 - progress) ** 3)
        self.queue_draw()
        if progress < 1:
            return GLib.SOURCE_CONTINUE
        self.tick = None
        return GLib.SOURCE_REMOVE
