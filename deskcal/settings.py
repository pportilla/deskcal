"""Settings window: calendars, placement and appearance.  Changes apply live."""

import subprocess
import threading
from datetime import date, timedelta

from gi.repository import Gdk, GLib, Gtk, Pango

from . import config, sources

EDS_PACKAGES = "sudo apt install gir1.2-ecal-2.0 gir1.2-edataserver-1.2"

GOOGLE_HELP = (
    "<b>Google:</b> calendar.google.com → ⚙ Settings → pick a calendar under "
    "<i>Settings for my calendars</i> → <i>Integrate calendar</i> → copy "
    "<i>Secret address in iCal format</i>.\n"
    "<b>Outlook / Microsoft 365:</b> Settings → Calendar → Shared calendars → "
    "<i>Publish a calendar</i> → copy the ICS link.\n"
    "<b>Nextcloud / iCloud / others:</b> use the calendar's subscription (.ics / webcal) link.")


def _hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


def _rgba(color):
    rgba = Gdk.RGBA()
    rgba.parse(sources.normalize_color(color) or sources.PALETTE[0])
    return rgba


def _heading(text):
    lbl = Gtk.Label(xalign=0)
    lbl.set_markup(f"<b>{text}</b>")
    lbl.set_margin_top(6)
    return lbl


def _note(markup):
    lbl = Gtk.Label(xalign=0)
    lbl.set_markup(markup)
    lbl.set_line_wrap(True)
    lbl.set_max_width_chars(70)
    lbl.get_style_context().add_class("dim-label")
    return lbl


def _swatch(color):
    lbl = Gtk.Label()
    color = sources.normalize_color(color) or sources.PALETTE[0]  # comes from a remote calendar
    lbl.set_markup(f'<span foreground="{color}" size="large">●</span>')
    return lbl


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="DeskCal Settings")
        self.app = app
        self.set_default_size(620, 680)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("x-office-calendar")
        self._save_source = None
        self._apply_source = None

        notebook = Gtk.Notebook()
        notebook.append_page(self._calendars_page(), Gtk.Label(label="Calendars"))
        notebook.append_page(self._appearance_page(), Gtk.Label(label="Position & appearance"))
        self.add(notebook)

    @property
    def cfg(self):
        return self.app.cfg

    def _changed(self, refetch=False):
        """Apply to the widget (right away, or debounced when it means refetching) and save."""
        if refetch:
            if self._apply_source:
                GLib.source_remove(self._apply_source)
            self._apply_source = GLib.timeout_add(600, self._apply_refetch)
        elif self.app.window:
            self.app.window.apply_config(refetch=False)
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(400, self._save)

    def _apply_refetch(self):
        self._apply_source = None
        if self.app.window:
            self.app.window.apply_config(refetch=True)
        return False

    def _save(self):
        self._save_source = None
        config.save(self.cfg)
        return False

    # -------------------------------------------------------------- calendars

    def _calendars_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(18)

        box.pack_start(_heading("Online accounts"), False, False, 0)
        box.pack_start(_note(
            "Gmail / Google, Microsoft 365, Nextcloud and CalDAV accounts added in "
            "<i>GNOME Settings → Online Accounts</i> show up here automatically. "
            "Add as many Google accounts as you like, then tick the calendars to show."), False, False, 0)
        self.eds_list = Gtk.ListBox()
        self.eds_list.set_selection_mode(Gtk.SelectionMode.NONE)
        frame = Gtk.Frame()
        frame.add(self.eds_list)
        box.pack_start(frame, False, False, 0)
        row = Gtk.Box(spacing=6)
        add_acc = Gtk.Button(label="Add account…")
        add_acc.connect("clicked", self._open_online_accounts)
        reload_btn = Gtk.Button(label="Reload list")
        reload_btn.connect("clicked", lambda *_: (self._fill_eds(), self.app.window and self.app.window.refresh(True)))
        row.pack_start(add_acc, False, False, 0)
        row.pack_start(reload_btn, False, False, 0)
        self.eds_buttons = row
        box.pack_start(row, False, False, 0)

        gbox = Gtk.Box(spacing=12)
        gbox.set_margin_top(6)
        gsw = Gtk.Switch()
        gsw.set_active(bool(self.cfg["google_event_colors"]))
        gsw.set_valign(Gtk.Align.START)
        gsw.connect("notify::active", lambda w, _p: self._set("google_event_colors", w.get_active(), refetch=True))
        gbox.pack_start(gsw, False, False, 0)
        gbox.pack_start(_note(
            "<b>Google event colours</b>: give each Google event the colour it has in Google Calendar. "
            "Google leaves these out of the normal calendar feed, so DeskCal asks the Google Calendar API "
            "using your Online Accounts login. It only reads: for each event its id, start time and colour, and the "
            "colour labels of the calendar (no titles or attendees); the login token is never stored."), True, True, 0)
        box.pack_start(gbox, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 8)
        box.pack_start(_heading("Calendar links (iCal)"), False, False, 0)
        box.pack_start(_note(
            "Works without any account setup. Links are kept in your GNOME keyring.\n" + GOOGLE_HELP),
            False, False, 0)
        self.ics_list = Gtk.ListBox()
        self.ics_list.set_selection_mode(Gtk.SelectionMode.NONE)
        frame = Gtk.Frame()
        frame.add(self.ics_list)
        box.pack_start(frame, False, False, 0)
        add_link = Gtk.Button(label="Add calendar link…")
        add_link.connect("clicked", lambda *_: AddFeedDialog(self).show_all())
        add_link.set_halign(Gtk.Align.START)
        box.pack_start(add_link, False, False, 0)

        self._fill_eds()
        self.fill_ics()
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(box)
        return scroller

    def _placeholder(self, listbox, text):
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(text)
        lbl.set_line_wrap(True)
        lbl.set_selectable(True)
        lbl.set_margin_start(12)
        lbl.set_margin_end(12)
        lbl.set_margin_top(10)
        lbl.set_margin_bottom(10)
        listbox.add(lbl)

    def _fill_eds(self):
        for child in self.eds_list.get_children():
            child.destroy()
        if not sources.eds_available():
            self.eds_buttons.set_sensitive(False)
            self._placeholder(self.eds_list,
                              "The Online Accounts connector needs two small system packages:\n"
                              f"<tt>{EDS_PACKAGES}</tt>\nInstall them, then restart DeskCal.")
            self.eds_list.show_all()
            return
        try:
            cals = sources.list_eds_calendars()
        except GLib.Error as e:
            cals = []
            self._placeholder(self.eds_list, f"Could not read online accounts: {GLib.markup_escape_text(e.message, -1)}")
        hidden = self.cfg.setdefault("eds_hidden", [])
        for cal in sorted(cals, key=lambda c: (c.backend in ("local", "contacts"), c.account.lower(), c.name.lower())):
            row = Gtk.Box(spacing=10)
            row.set_border_width(8)
            check = Gtk.CheckButton()
            check.set_active(cal.uid not in hidden)
            check.connect("toggled", self._eds_toggled, cal.uid)
            row.pack_start(check, False, False, 0)
            row.pack_start(_swatch(cal.color), False, False, 0)
            name = Gtk.Label(label=cal.name, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(name, True, True, 0)
            acc = Gtk.Label(label=cal.account, xalign=1)
            acc.get_style_context().add_class("dim-label")
            row.pack_start(acc, False, False, 0)
            self.eds_list.add(row)
        if not any(c.backend not in ("local", "contacts") for c in cals):
            self._placeholder(self.eds_list,
                              "<i>No online accounts with calendars yet. Click “Add account…”, choose "
                              "Google (or another provider), sign in and keep “Calendar” switched on.</i>")
        self.eds_list.show_all()

    def _eds_toggled(self, check, uid):
        hidden = self.cfg.setdefault("eds_hidden", [])
        if check.get_active() and uid in hidden:
            hidden.remove(uid)
        elif not check.get_active() and uid not in hidden:
            hidden.append(uid)
        self._changed(refetch=True)

    def _open_online_accounts(self, *_):
        for cmd in (["gnome-control-center", "online-accounts"], ["gnome-online-accounts-gtk"]):
            try:
                subprocess.Popen(cmd, start_new_session=True)
                return
            except FileNotFoundError:
                continue

    def fill_ics(self):
        for child in self.ics_list.get_children():
            child.destroy()
        feeds = self.cfg.setdefault("ics", [])
        for feed in feeds:
            row = Gtk.Box(spacing=10)
            row.set_border_width(6)
            check = Gtk.CheckButton()
            check.set_active(feed.get("enabled", True))
            check.connect("toggled", self._feed_toggled, feed)
            row.pack_start(check, False, False, 4)
            color = Gtk.ColorButton.new_with_rgba(_rgba(feed.get("color")))
            color.set_use_alpha(False)
            color.connect("color-set", self._feed_color, feed)
            row.pack_start(color, False, False, 0)
            name = Gtk.Entry(text=feed.get("name", ""))
            name.connect("changed", self._feed_renamed, feed)
            row.pack_start(name, True, True, 0)
            remove = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
            remove.set_tooltip_text("Remove this calendar")
            remove.connect("clicked", self._feed_removed, feed)
            row.pack_start(remove, False, False, 0)
            self.ics_list.add(row)
        if not feeds:
            self._placeholder(self.ics_list, "<i>No calendar links added.</i>")
        self.ics_list.show_all()

    def _feed_toggled(self, check, feed):
        feed["enabled"] = check.get_active()
        self._changed(refetch=True)

    def _feed_color(self, button, feed):
        feed["color"] = _hex(button.get_rgba())
        self._changed(refetch=True)

    def _feed_renamed(self, entry, feed):
        feed["name"] = entry.get_text().strip()
        self._changed(refetch=True)

    def _feed_removed(self, _btn, feed):
        config.forget_feed_url(feed)
        self.cfg["ics"] = [f for f in self.cfg["ics"] if f["id"] != feed["id"]]
        self.fill_ics()
        self._changed(refetch=True)

    def add_feed(self, name, url, color):
        feed = {"id": config.new_feed_id(), "name": name, "color": color, "enabled": True}
        config.store_feed_url(feed, url)
        self.cfg.setdefault("ics", []).append(feed)
        self.fill_ics()
        self._changed(refetch=True)

    # ------------------------------------------------------------ appearance

    def _appearance_page(self):
        grid = Gtk.Grid(column_spacing=16, row_spacing=10)
        grid.set_border_width(18)
        row = 0

        def add(text, widget, note=None):
            nonlocal row
            lbl = Gtk.Label(label=text, xalign=1)
            grid.attach(lbl, 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            row += 1
            if note:
                n = _note(note)
                grid.attach(n, 1, row, 1, 1)
                row += 1

        # Monitor
        monitor = Gtk.ComboBoxText()
        monitor.append("secondary", "Secondary monitor (automatic)")
        monitor.append("primary", "Primary monitor")
        display = Gdk.Display.get_default()
        for i in range(display.get_n_monitors()):
            m = display.get_monitor(i)
            g = m.get_geometry()
            extra = " · primary" if m.is_primary() else ""
            monitor.append(m.get_model(), f"{m.get_model()} ({g.width}×{g.height}{extra})")
        if not monitor.set_active_id(self.cfg["monitor"]):
            monitor.set_active_id("secondary")
        monitor.connect("changed", lambda w: self._set("monitor", w.get_active_id()))
        add("Monitor", monitor)

        anchor = Gtk.ComboBoxText()
        for a in config.ANCHORS:
            anchor.append(a, a.replace("-", " ").capitalize())
        anchor.append("custom", "Custom (set with Move / resize)")
        anchor.set_active_id(self.cfg["anchor"])
        anchor.connect("changed", lambda w: self._set("anchor", w.get_active_id()))
        add("Position", anchor)

        edit = Gtk.Button(label="Move / resize on the desktop…")
        edit.set_halign(Gtk.Align.START)
        edit.connect("clicked", lambda *_: self.app.start_edit())
        add("", edit, "Drag the widget to place it and drag its edges to resize it, then click Done. "
                      "Afterwards it stays fixed in that place.")

        add("Width (px)", self._spin("width", 0, 7680, 10), "0 = full width of the monitor")
        add("Height (px)", self._spin("height", 0, 4320, 10), "0 = full height of the monitor")
        add("Margin (px)", self._spin("margin", 0, 400, 2))

        theme = Gtk.ComboBoxText()
        theme.append("dark", "Dark")
        theme.append("light", "Light")
        theme.set_active_id(self.cfg["theme"])
        theme.connect("changed", lambda w: self._set("theme", w.get_active_id()))
        add("Theme", theme)

        opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.2, 1.0, 0.02)
        opacity.set_value(float(self.cfg["opacity"]))
        opacity.set_digits(2)
        opacity.connect("value-changed", lambda w: self._set("opacity", round(w.get_value(), 2)))
        add("Background opacity", opacity)

        add("Show clock", self._switch("show_clock"))
        add("24-hour time", self._switch("clock_24h"))
        add("Week starts on Monday", self._switch("week_starts_monday", refetch=True))
        layout = Gtk.ComboBoxText()
        layout.append("single", "Single view")
        layout.append("split", "Split: top view + bottom view")
        layout.set_active_id(self.cfg["layout"])
        layout.connect("changed", lambda w: self._on_layout(w.get_active_id()))
        self.layout_combo = layout
        add("Layout", layout, "Split shows a month or week on top and a day (or the list) below it. "
                              "Clicking a day in the top view shows it in the bottom one.")

        self.view_combo = self._view_combo("view", (("month", "Month"), ("week", "Week"),
                                                    ("day", "Day"), ("list", "List")))
        add("View", self.view_combo)
        self.top_combo = self._view_combo("split_top", (("month", "Month"), ("week", "Week")))
        add("Top view", self.top_combo)
        self.bottom_combo = self._view_combo("split_bottom", (("day", "Day"), ("list", "List")))
        add("Bottom view", self.bottom_combo)
        ratio = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.15, 0.85, 0.01)
        ratio.set_value(float(self.cfg["split_ratio"]))
        ratio.set_digits(2)
        ratio.connect("format-value", lambda _s, v: f"{round(v * 100)}%")
        ratio.connect("value-changed", lambda w: self._set("split_ratio", round(w.get_value(), 2)))
        self.ratio_scale = ratio
        add("Top share of the height", ratio)
        self._update_layout_rows()
        add("Days in list view", self._spin("agenda_days", 1, 90, 1, refetch=True))
        add("Refresh every (minutes)", self._spin("refresh_minutes", 1, 240, 1))

        hint = _note("Tip: right-click the widget for Refresh / Settings / Quit. "
                     "Launching DeskCal again while it runs opens this window.")
        grid.attach(hint, 0, row, 2, 1)
        return grid

    def _view_combo(self, key, choices):
        combo = Gtk.ComboBoxText()
        for value, text in choices:
            combo.append(value, text)
        combo.set_active_id(self.cfg[key])
        combo.connect("changed", lambda w: self._set(key, w.get_active_id()))
        return combo

    def _on_layout(self, value):
        self._set("layout", value)
        self._update_layout_rows()

    def _update_layout_rows(self):
        split = self.cfg["layout"] == "split"
        self.view_combo.set_sensitive(not split)
        for w in (self.top_combo, self.bottom_combo, self.ratio_scale):
            w.set_sensitive(split)

    def sync_layout(self):
        """The layout or a view was changed on the widget itself: follow it."""
        self.layout_combo.set_active_id(self.cfg["layout"])
        self.view_combo.set_active_id(self.cfg["view"])
        self.top_combo.set_active_id(self.cfg["split_top"])
        self.bottom_combo.set_active_id(self.cfg["split_bottom"])
        self._update_layout_rows()

    def _set(self, key, value, refetch=False):
        if value is None or self.cfg.get(key) == value:
            return
        self.cfg[key] = value
        self._changed(refetch=refetch)

    def _spin(self, key, lo, hi, step, refetch=False):
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_value(int(self.cfg[key]))
        spin.connect("value-changed", lambda w: self._set(key, int(w.get_value()), refetch))
        return spin

    def _switch(self, key, refetch=False):
        sw = Gtk.Switch()
        sw.set_active(bool(self.cfg[key]))
        sw.set_halign(Gtk.Align.START)
        sw.connect("notify::active", lambda w, _p: self._set(key, w.get_active(), refetch))
        return sw


class AddFeedDialog(Gtk.Dialog):
    def __init__(self, settings):
        super().__init__(title="Add calendar link", transient_for=settings, modal=True)
        self.settings = settings
        self.set_default_size(560, -1)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.ok = self.add_button("Add", Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_border_width(16)
        self.url = Gtk.Entry(placeholder_text="https://calendar.google.com/calendar/ical/…/basic.ics")
        self.url.set_activates_default(True)
        self.url.set_hexpand(True)
        self.name = Gtk.Entry(placeholder_text="Optional, taken from the calendar if empty")
        used = {f.get("color") for f in settings.cfg.get("ics", [])}
        color = next((c for c in sources.PALETTE if c not in used), sources.PALETTE[0])
        self.color = Gtk.ColorButton.new_with_rgba(_rgba(color))
        self.color.set_halign(Gtk.Align.START)
        # Untouched, the colour the calendar announces for itself (if any) is used.
        self._color_touched = False
        self.color.connect("color-set", lambda *_: setattr(self, "_color_touched", True))
        self.color.set_tooltip_text("Left as it is, the calendar's own colour is used when it has one")
        for i, (text, w) in enumerate((("iCal link", self.url), ("Name", self.name), ("Colour", self.color))):
            grid.attach(Gtk.Label(label=text, xalign=1), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        grid.attach(self.message, 1, 3, 1, 1)
        self.spinner = Gtk.Spinner()
        grid.attach(self.spinner, 0, 3, 1, 1)
        self.get_content_area().add(grid)
        self.connect("response", self._on_response)
        self._closed = False  # cancelled while the link was still being checked
        self.connect("destroy", lambda *_: setattr(self, "_closed", True))

    def _on_response(self, _dlg, response):
        if response != Gtk.ResponseType.OK:
            self.destroy()
            return
        url = self.url.get_text().strip()
        if not url:
            self.message.set_markup("<b>Paste the calendar's iCal (.ics) link.</b>")
            return
        self.ok.set_sensitive(False)
        self.spinner.start()
        self.message.set_text("Checking the link…")
        threading.Thread(target=self._check, args=(url,), daemon=True).start()

    def _check(self, url):
        try:
            data = sources.download_ics(url)
            sources.parse_ics(data, "", "", *_probe_range())
            name, color = sources.ics_calendar_meta(data)
            GLib.idle_add(self._checked, url, name, color, None)
        except Exception as e:
            GLib.idle_add(self._checked, url, "", "", sources.error_text(e))

    def _checked(self, url, cal_name, cal_color, error):
        if self._closed:
            return False
        self.spinner.stop()
        self.ok.set_sensitive(True)
        if error:
            self.message.set_markup(f"<b>Could not use this link:</b> {GLib.markup_escape_text(error, -1)}")
            return False
        name = self.name.get_text().strip() or cal_name or "Calendar"
        color = cal_color if cal_color and not self._color_touched else _hex(self.color.get_rgba())
        self.settings.add_feed(name, url, color)
        self.destroy()
        return False


def _probe_range():
    today = date.today()
    return today - timedelta(days=31), today + timedelta(days=62)
