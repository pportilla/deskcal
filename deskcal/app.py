"""Gtk.Application: single instance; launching again opens Settings."""

from gi.repository import Gio, GLib, Gtk

from . import config, sources
from .settings import SettingsWindow
from .widget import DeskCalWindow


class DeskCalApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=config.APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("settings", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the settings window", None)
        self.add_main_option("edit", ord("e"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Move / resize the widget", None)
        self.add_main_option("quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Quit the running widget", None)
        self.first_run = not config.CONFIG_FILE.exists()
        self.cfg = config.load()
        self.window = None
        self.settings = None
        self._save_source = None
        self._reopen_settings = False

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if "quit" in options:
            self.quit()
            return 0
        first = self.window is None
        if first:
            self.window = DeskCalWindow(self)
            self.window.show_all()
        if "edit" in options:
            self.start_edit()
        elif "settings" in options or not first or (self.first_run and self._nothing_configured()):
            self.show_settings()
        return 0

    def _nothing_configured(self):
        if self.cfg.get("ics"):
            return False
        try:
            return not any(c.backend not in ("local", "contacts") for c in sources.list_eds_calendars())
        except GLib.Error:
            return True

    def show_settings(self):
        if self.window is not None and self.window.editing:
            # Settings would fight the move / resize in progress: open it once that is done.
            self._reopen_settings = True
            self.window.present()
            return
        if self.settings is None:
            self.settings = SettingsWindow(self)
            self.settings.connect("destroy", self._settings_closed)
            self.settings.show_all()
        self.settings.present()

    def _settings_closed(self, *_):
        config.save(self.cfg)
        self.settings = None

    def save_config_soon(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(400, self._save_now)

    def _save_now(self):
        self._save_source = None
        config.save(self.cfg)
        return False

    def start_edit(self):
        """Move / resize mode.  Settings is closed meanwhile so it does not cover the widget."""
        if self.settings is not None:
            self._reopen_settings = True
            self.settings.destroy()
        self.window.start_edit()

    def edit_finished(self):
        if self._reopen_settings:
            self._reopen_settings = False
            self.show_settings()
