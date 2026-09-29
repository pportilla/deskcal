#!/usr/bin/env bash
# Install DeskCal for the current user (no root needed).
#
#   ./install.sh               install / update
#   ./install.sh --autostart   ... and start it automatically at login
#   ./install.sh --uninstall   remove it (settings in ~/.config/deskcal and calendar links
#                              kept in the keyring stay; the saved-calendar cache is deleted)
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/deskcal"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_ID="io.github.deskcal.DeskCal.desktop"

autostart=0
for arg in "$@"; do
    case "$arg" in
        --autostart) autostart=1 ;;
        --uninstall)
            "$BIN/deskcal" --quit 2>/dev/null || true
            rm -rf "$DEST" "$BIN/deskcal" "$APPS/$DESKTOP_ID" "$AUTOSTART/$DESKTOP_ID" \
                   "${XDG_CACHE_HOME:-$HOME/.cache}/deskcal"
            echo "DeskCal removed. Kept: your settings (~/.config/deskcal) and any calendar links in the keyring."
            exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

mkdir -p "$DEST" "$BIN" "$APPS"
rm -rf "$DEST/deskcal"
cp -r "$SRC/deskcal" "$DEST/"
find "$DEST/deskcal" -name __pycache__ -prune -exec rm -rf {} +

# The venv sees the system PyGObject/GTK; only the iCal libraries come from PyPI.
[ -x "$DEST/venv/bin/python" ] || python3 -m venv --system-site-packages "$DEST/venv"
"$DEST/venv/bin/pip" install --quiet --upgrade -r "$SRC/requirements.txt"

cat > "$BIN/deskcal" <<EOF
#!/bin/sh
PYTHONPATH="$DEST" exec "$DEST/venv/bin/python" -m deskcal "\$@"
EOF
chmod +x "$BIN/deskcal"

entry() {
    cat <<EOF
[Desktop Entry]
Type=Application
Name=DeskCal
Comment=Calendar widget for your desktop (Google, Microsoft 365, CalDAV, iCal)
Exec="$BIN/deskcal"
Icon=x-office-calendar
Categories=Office;Calendar;
StartupWMClass=deskcal
Actions=settings;edit;quit;
$1

[Desktop Action settings]
Name=Settings
Exec="$BIN/deskcal" --settings

[Desktop Action edit]
Name=Move / resize
Exec="$BIN/deskcal" --edit

[Desktop Action quit]
Name=Quit
Exec="$BIN/deskcal" --quit
EOF
}

entry "" > "$APPS/$DESKTOP_ID"
if [ "$autostart" = 1 ]; then
    mkdir -p "$AUTOSTART"
    entry "X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5" > "$AUTOSTART/$DESKTOP_ID"
fi

echo "DeskCal installed in $DEST"
echo "Start it from the app grid (DeskCal) or run: $BIN/deskcal"
[ "$autostart" = 1 ] && echo "It will also start automatically when you log in."
if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.NameHasOwner io.github.deskcal.DeskCal 2>/dev/null | grep -q true; then
    echo
    echo "DeskCal is running the previous version. Restart it to use this one:"
    echo "  $BIN/deskcal --quit; $BIN/deskcal"
fi
python3 - <<'EOF' || true
import gi
try:
    gi.require_version("ECal", "2.0"); gi.require_version("EDataServer", "1.2")
except ValueError:
    print("\nOptional, for Gmail / Online Accounts calendars:\n"
          "  sudo apt install gir1.2-ecal-2.0 gir1.2-edataserver-1.2")
EOF
