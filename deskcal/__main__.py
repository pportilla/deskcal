import os
import sys

# GNOME on Wayland does not let native Wayland windows choose their monitor,
# position or "stay below" state, but it honours all of that for X11 clients.
# So the widget runs through XWayland.  The variable is dropped again right
# after GTK has opened the display so programs we launch (browser, GNOME
# Settings) start natively.
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

GLib.set_prgname("deskcal")
GLib.set_application_name("DeskCal")

from gi.repository import Gtk  # noqa: E402,F401  (opens the X11 display)

del os.environ["GDK_BACKEND"]

from .app import DeskCalApp  # noqa: E402


def main():
    return DeskCalApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
