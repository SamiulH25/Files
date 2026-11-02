#!/bin/bash
# Makes Files the desktop's file manager. Undo with: ./install.sh --uninstall
#
# Changes, all per-user and reversible:
#   ~/.local/bin/files                         link to this checkout
#   ~/.local/share/applications/               launcher entry
#   ~/.local/share/dbus-1/services/            "show in folder" from browsers and portals
#   $XDG_CONFIG_HOME/mimeapps.list             default handler for folders (through xdg-mime)
#   $XDG_CONFIG_HOME/hypr/bindings.lua         SUPER+SHIFT+F and SUPER+ALT+SHIFT+F, in a marked block
set -euo pipefail

project="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
app_id="io.github.Files"
bin="$HOME/.local/bin/files"
desktop="$HOME/.local/share/applications/$app_id.desktop"
service="$HOME/.local/share/dbus-1/services/org.freedesktop.FileManager1.service"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"  # where xdg-mime writes
bindings="$config_home/hypr/bindings.lua"
saved_default="${XDG_STATE_HOME:-$HOME/.local/state}/files/previous-folder-handler"
begin="-- BEGIN files file manager"
end="-- END files file manager"

remove_bindings() {
  if [[ -f $bindings ]]; then
    sed -i "/^$begin\$/,/^$end\$/d" "$bindings"
  fi
}

reload_hyprland() {
  if command -v hyprctl >/dev/null && [[ -n ${XDG_RUNTIME_DIR:-} && -d $XDG_RUNTIME_DIR/hypr ]]; then
    hyprctl reload >/dev/null 2>&1 || true
  fi
}

if [[ ${1:-} == "--uninstall" ]]; then
  remove_bindings
  rm -f "$bin" "$desktop" "$service"
  # Hand folders back to whatever had them: a default you set yourself, or else the system's.
  if [[ -f $config_home/mimeapps.list ]]; then
    sed -i "/^inode\/directory=$app_id\.desktop\$/d" "$config_home/mimeapps.list"
  fi
  if [[ -s $saved_default ]]; then
    xdg-mime default "$(cat "$saved_default")" inode/directory
  fi
  rm -f "$saved_default"
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  reload_hyprland
  echo "Removed. The previous file manager is the default again."
  exit 0
fi

mkdir -p "$(dirname "$bin")" "$(dirname "$desktop")" "$(dirname "$service")"
chmod +x "$project/files"
ln -sfn "$project/files" "$bin"

cat >"$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Files
Comment=Browse files and jump to folders you use often
Exec=$bin %U
Icon=system-file-manager
Terminal=false
Categories=System;FileTools;FileManager;
MimeType=inode/directory;
StartupWMClass=$app_id
EOF

# Overrides the system's copy of this service, which starts Nautilus.
cat >"$service" <<EOF
[D-BUS Service]
Name=org.freedesktop.FileManager1
Exec=$bin --gapplication-service
EOF

previous="$(sed -n 's/^inode\/directory=//p' "$config_home/mimeapps.list" 2>/dev/null | head -n1)"
if [[ -n $previous && $previous != "$app_id.desktop" ]]; then
  mkdir -p "$(dirname "$saved_default")"
  printf '%s\n' "$previous" >"$saved_default"
fi
xdg-mime default "$app_id.desktop" inode/directory
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if [[ -f $bindings ]]; then
  remove_bindings
  cat >>"$bindings" <<EOF
$begin
hl.unbind("SUPER + SHIFT + F")
hl.unbind("SUPER + ALT + SHIFT + F")
o.bind("SUPER + SHIFT + F", "File manager", "uwsm-app -- $bin")
o.bind("SUPER + ALT + SHIFT + F", "File manager (cwd)", 'uwsm-app -- $bin "\$(omarchy-cmd-terminal-cwd)"')
$end
EOF
  reload_hyprland
else
  echo "No $bindings found, so the keyboard shortcuts were left alone."
fi

echo "Installed. SUPER+SHIFT+F now opens Files."
