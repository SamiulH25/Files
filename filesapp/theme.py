"""Builds the stylesheet from the active Omarchy theme, so every theme just works."""
import os
import re
import subprocess
import tomllib

THEME_DIR = os.path.expanduser("~/.local/state/omarchy/current")
HEX = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
MIN_TEXT_CONTRAST = 4.5  # WCAG AA for small text

# One Dark values, for machines without Omarchy.
FALLBACK = {
    "mode": "dark", "accent": "#61afef", "selection": "#3e4451", "muted": "#5c6370",
    "background": "#282c34", "dark_background": "#21252b", "darker_background": "#181a1f",
    "lighter_background": "#3e4451", "foreground": "#abb2bf", "bright_foreground": "#c8ccd4",
    "red": "#e06c75", "yellow": "#e5c07b", "orange": "#d19a66", "green": "#98c379",
    "cyan": "#56b6c2", "blue": "#61afef", "magenta": "#c678dd",
}


def load_palette(path=None):
    palette = dict(FALLBACK)
    try:
        with open(path or os.path.join(THEME_DIR, "theme", "colors.toml"), "rb") as f:
            saved = tomllib.load(f)
    except (OSError, ValueError):
        return palette
    # Themes can come from git repos and these values end up in CSS, so only real colours get through.
    palette.update({k: v for k, v in saved.items() if k in FALLBACK and isinstance(v, str) and HEX.match(v)})
    if saved.get("mode") == "light":
        palette["mode"] = "light"
    if "orange" not in saved:  # a few themes omit it
        palette["orange"] = palette["yellow"]
    return palette


def system_font():
    try:
        out = subprocess.run(["omarchy-font-current"], capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return re.sub(r'["\\;{}]', "", out) or "monospace"


def _rgb(color):
    return [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]


def _luminance(rgb):
    def linear(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = [linear(c) for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def secondary_text(palette):
    """A quiet text colour that stays readable: `muted` moved towards `foreground` only as far as needed.

    Many themes make `muted` too faint for small text, so it can't be used as is.
    """
    muted, fg = _rgb(palette["muted"]), _rgb(palette["foreground"])
    # Checked against both main surfaces, because the sidebar is the darker (or, on light themes, tinted) one.
    surfaces = [_rgb(palette["background"]), _rgb(palette["darker_background"])]
    mix = muted
    for step in range(11):
        t = step / 10
        mix = [m + (f - m) * t for m, f in zip(muted, fg)]
        if min(contrast(mix, s) for s in surfaces) >= MIN_TEXT_CONTRAST:
            break
    return "#" + "".join(f"{round(c * 255):02x}" for c in mix)


def build_css(palette, font):
    colors = "\n".join(
        f"@define-color {name} {value};" for name, value in palette.items() if name != "mode"
    )
    colors += f"\n@define-color secondary {secondary_text(palette)};"
    return colors + STYLE.replace("$FONT", f'"{font}", "JetBrainsMono Nerd Font", monospace')


STYLE = """
window.files {
  background: transparent;
  color: @foreground;
  font-family: $FONT;
  font-size: 13px;
}
window.files * { font-family: $FONT; }

.content { filter: blur(0px); transition: filter 130ms ease-out; }
.content.behind { filter: blur(9px); }

/* Sidebar sits on the compositor's blur, so the wallpaper reads through it. */
.sidebar { background: alpha(@darker_background, 0.80); padding: 12px 0 8px 0; }
.main { background: alpha(@background, 0.94); }

.side-head { color: @secondary; font-size: 12px; padding: 14px 16px 4px 16px; }
.side-row {
  background: none; border: none; box-shadow: none; border-radius: 0;
  color: @foreground; padding: 4px 16px 4px 13px; min-height: 22px;
  border-left: 3px solid transparent;
}
.side-row:hover { background: alpha(@foreground, 0.08); color: @bright_foreground; }
.side-row.current { border-left-color: @accent; color: @bright_foreground; background: alpha(@accent, 0.12); }
.side-row:drop(active) { background: alpha(@accent, 0.28); box-shadow: none; }
.side-glyph { color: @secondary; min-width: 18px; font-size: 13px; }
.side-row.current .side-glyph { color: @accent; }
.side-use { min-height: 2px; margin: 0 16px 3px 34px; }
.side-use trough { background: alpha(@foreground, 0.12); min-height: 2px; border: none; border-radius: 0; }
.side-use progress { background: @accent; min-height: 2px; border: none; border-radius: 0; }
.side-use.full progress { background: @red; }
.side-eject { background: none; border: none; box-shadow: none; color: @secondary; padding: 0 12px; min-height: 0; min-width: 0; border-radius: 0; }
.side-eject:hover { color: @bright_foreground; }

.header { padding: 12px 18px 6px 16px; }
.crumb {
  background: none; border: none; box-shadow: none; border-radius: 0;
  color: @secondary; padding: 0 1px; min-height: 0; min-width: 0; font-size: 12px;
}
.crumb:hover { color: @accent; }
.crumb-sep { color: @secondary; font-size: 12px; padding: 0 3px; }
.title { font-size: 36px; font-weight: 800; color: @bright_foreground; letter-spacing: -1px; }

.tool-group { background: alpha(@foreground, 0.05); box-shadow: inset 0 0 0 1px alpha(@foreground, 0.08); }
.tool {
  background: none; border: none; box-shadow: none; border-radius: 0;
  color: @secondary; padding: 5px 7px; min-height: 0; min-width: 0;
  transition: color 140ms ease-out, background-color 140ms ease-out, box-shadow 180ms ease-out;
}
.tool:hover { color: @bright_foreground; background: alpha(@foreground, 0.10); }
.tool:active { color: @accent; background: alpha(@accent, 0.26); }
.tool:focus-visible { outline: 1px solid @accent; outline-offset: -1px; }
.tool:disabled { color: alpha(@secondary, 0.35); }
.tool.on { color: @accent; background: alpha(@accent, 0.14); box-shadow: inset 0 -2px 0 @accent, 0 2px 12px -3px alpha(@accent, 0.7); }
.tool.on:hover { color: @accent; background: alpha(@accent, 0.24); }

.columns { padding: 2px 18px 3px 16px; border-bottom: 1px solid alpha(@foreground, 0.10); }
.column {
  background: none; border: none; box-shadow: none; border-radius: 0;
  color: @secondary; padding: 0; min-height: 0; min-width: 0; font-size: 12px;
}
.column:hover { color: @bright_foreground; }
.column.on { color: @foreground; }

listview, gridview { background: none; color: @foreground; }
listview > row { padding: 0; min-height: 0; background: none; border-left: 3px solid transparent; }
listview > row:hover { background: alpha(@foreground, 0.05); }
listview > row:selected { background: alpha(@accent, 0.20); border-left-color: @accent; color: @bright_foreground; }
listview > row:focus-visible { outline: 1px solid alpha(@accent, 0.75); outline-offset: -1px; }
gridview > child { padding: 0; background: none; border-radius: 0; }
gridview > child:hover { background: alpha(@foreground, 0.05); }
gridview > child:selected { background: alpha(@accent, 0.20); color: @bright_foreground; }
gridview > child:focus-visible { outline: 1px solid alpha(@accent, 0.75); outline-offset: -1px; }
rubberband { background: alpha(@accent, 0.16); border: 1px solid @accent; border-radius: 0; }

.row { padding: 3px 18px 3px 13px; min-height: 22px; }
.row:drop(active), .cell:drop(active) { background: alpha(@accent, 0.30); box-shadow: inset 0 0 0 1px @accent; }
.glyph { min-width: 20px; font-size: 14px; }
.name { color: @foreground; }
.is-dir .name { color: @bright_foreground; font-weight: 600; }
listview > row:selected .name, gridview > child:selected .name { color: @bright_foreground; }
listview > row:selected .is-hidden, gridview > child:selected .is-hidden { opacity: 1; }
.is-hidden { opacity: 0.68; }
.note, .size, .when { color: @secondary; font-size: 12px; }

.cell { padding: 10px 8px 8px 8px; }
.cell .glyph { font-size: 50px; min-height: 84px; }
.cell .name { font-size: 12px; margin-top: 4px; }
.thumb { min-height: 84px; background: alpha(@darker_background, 0.6); }

.k-folder { color: @accent; }
.k-image { color: @magenta; }
.k-video { color: @red; }
.k-audio { color: @cyan; }
.k-archive { color: @orange; }
.k-pdf { color: @red; }
.k-code { color: @green; }
.k-doc { color: @blue; }
.k-sheet { color: @green; }
.k-slides { color: @orange; }
.k-exec { color: @yellow; }
.k-text { color: @foreground; }
.k-file { color: @secondary; }

.empty { color: @secondary; font-size: 14px; }
.status { padding: 4px 18px 5px 16px; color: @secondary; font-size: 12px; border-top: 1px solid alpha(@foreground, 0.10); }

.job { padding: 6px 18px 6px 16px; background: alpha(@darker_background, 0.7); border-top: 1px solid alpha(@foreground, 0.10); font-size: 12px; }
.job progressbar trough { background: alpha(@foreground, 0.14); min-height: 3px; border: none; border-radius: 0; }
.job progressbar progress { background: @accent; min-height: 3px; border: none; border-radius: 0; }

.toast { background: @dark_background; color: @bright_foreground; padding: 8px 8px 8px 16px; margin: 0 0 40px 0; border: 1px solid @lighter_background; border-left: 3px solid @accent; }
.toast button { background: none; border: none; box-shadow: none; color: @accent; font-weight: 700; padding: 2px 10px; min-height: 0; border-radius: 0; }
.toast button:hover { background: alpha(@accent, 0.16); }

/* Quick search: size follows how often a folder is used, set per row in code. */
.jump { background: alpha(@darker_background, 0.74); padding: 56px 88px 40px 88px; }
.jump-entry, .jump-entry:focus, .jump-entry:focus-within {
  background: none; border: none; box-shadow: none; outline: none; border-radius: 0;
  font-size: 40px; font-weight: 800; color: @bright_foreground; caret-color: @accent;
  padding: 0 0 0 15px; min-height: 56px;
}
.jump-entry placeholder { color: @secondary; font-weight: 400; }
.jump-rule { min-height: 1px; background: alpha(@foreground, 0.16); margin: 8px 0 12px 0; }
.hit {
  background: none; border: none; box-shadow: none; border-radius: 0; outline: none;
  color: @foreground; padding: 1px 12px; min-height: 0; border-left: 3px solid transparent;
}
.hit:hover { background: alpha(@foreground, 0.07); }
.hit.current { background: alpha(@accent, 0.16); border-left-color: @accent; color: @bright_foreground; }
.hit-path { color: @secondary; font-size: 12px; }
.hit-here { color: @secondary; font-size: 12px; }
.jump-empty { color: @secondary; font-size: 15px; padding: 8px 15px; }

.scrim { background: alpha(@darker_background, 0.55); }
.sheet { background: @dark_background; border: 1px solid @lighter_background; border-top: 3px solid @accent; padding: 22px 24px 18px 24px; min-width: 460px; }
.sheet.danger { border-top-color: @red; }
.sheet-title { font-size: 18px; font-weight: 700; color: @bright_foreground; }
.sheet-body { color: @foreground; margin-top: 8px; }
.sheet check { margin-right: 8px; }
.act {
  background: alpha(@foreground, 0.08); border: none; box-shadow: none; border-radius: 0;
  color: @bright_foreground; padding: 6px 14px; min-height: 0;
}
.act:hover { background: alpha(@foreground, 0.16); }
.act:focus-visible { outline: 2px solid @accent; outline-offset: 1px; }
.act.primary { background: @accent; color: @darker_background; font-weight: 700; }
.act.danger { background: @red; color: @darker_background; font-weight: 700; }

popover.rename > contents { background: @dark_background; border: 1px solid @accent; border-radius: 0; padding: 10px; box-shadow: 0 6px 24px alpha(black, 0.4); }
popover.rename entry { background: @darker_background; color: @bright_foreground; border: none; border-radius: 0; box-shadow: none; outline: none; padding: 4px 8px; min-width: 300px; }
.rename-error { color: @red; font-size: 12px; margin-top: 6px; }

popover.menu > contents { background: @dark_background; color: @foreground; border: 1px solid @lighter_background; border-radius: 0; padding: 4px; box-shadow: 0 6px 24px alpha(black, 0.4); }
popover.menu modelbutton { border-radius: 0; padding: 4px 12px; min-height: 22px; color: @foreground; }
popover.menu modelbutton:hover, popover.menu modelbutton:selected { background: alpha(@accent, 0.20); color: @bright_foreground; }
popover.menu separator { background: alpha(@foreground, 0.12); margin: 4px 0; }
popover.menu accelerator { color: @secondary; }

tooltip { background: @dark_background; color: @bright_foreground; border: 1px solid @lighter_background; border-radius: 0; box-shadow: none; }
tooltip > box { padding: 2px 4px; }

scrollbar { background: none; border: none; }
scrollbar slider { background: alpha(@muted, 0.55); border-radius: 0; min-width: 5px; min-height: 5px; border: none; }
scrollbar slider:hover { background: @muted; }
selection { background: alpha(@accent, 0.35); color: @bright_foreground; }
"""
