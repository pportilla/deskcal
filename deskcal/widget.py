"""The borderless calendar window that sits on the desktop.

Normally the window is locked: fixed size and position, below all other
windows.  "Move / resize" switches to edit mode, where dragging anywhere moves
it and dragging an edge or corner resizes it; Done stores the geometry.
"""

import calendar
import threading
from datetime import date, datetime, timedelta

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import config, sources, style
from .views import (ListView, MonthView, TimeGridView, covers_whole_day, esc, flat_button,
                    label, linkify, local_midnight)

VIEWS = [("month", "Month"), ("week", "Week"), ("day", "Day"), ("list", "List")]
EDIT_MIN_SIZE = (360, 380)  # narrower than this the toolbar no longer fits
EDIT_MIN_HEIGHT_SPLIT = 600
# Split layout: the smallest useful panes.  Fixed numbers on purpose: measuring a
# view's minimum height would follow whatever size it currently has (the month
# grid shows as many event chips as fit), and then the divider could never move up.
TOP_MIN_HEIGHT = {"month": 212, "week": 140}
BOTTOM_MIN_HEIGHT = 150
E = Gdk.WindowEdge
EDGE_CURSORS = {E.NORTH_WEST: "nw-resize", E.NORTH: "n-resize", E.NORTH_EAST: "ne-resize",
                E.WEST: "w-resize", E.EAST: "e-resize", E.SOUTH_WEST: "sw-resize",
                E.SOUTH: "s-resize", E.SOUTH_EAST: "se-resize", None: "move"}


def safe_color(color):
    return sources.normalize_color(color) or sources.PALETTE[0]


class DeskCalWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="DeskCal")
        self.app = app
        self.events = []
        self.by_day = {}
        self.errors = []
        self.notes = []
        self.n_calendars = 0
        self.last_update = None
        self.today = date.today()
        self.cursor = self.today
        self.fetched_range = None
        self.fetching = False
        self.fetch_again = False
        self.popover = None
        self.menu = None
        self.editing = False
        self._edit_backup = None
        self._cursor_name = None
        self._expected_pos = None
        self._snaps = 0
        self._snap_source = None
        self._last_minute = None
        self._refresh_source = None
        self._range_source = None
        self._retry_source = None
        self._retry_delay = 90
        self._scroll_acc = 0.0
        self._layout_sig = None
        self._render_pending = False
        self._shown_views = []
        self._paned_h = 0
        self._ratio_key = None

        self._setup_window()
        self.css = Gtk.CssProvider()
        self.color_css = Gtk.CssProvider()  # one class per calendar colour
        self.colors = None
        for provider in (self.css, self.color_css):
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._build()

        GLib.timeout_add_seconds(10, self._tick)
        self._watch_resume()
        self.apply_config()

    @property
    def cfg(self):
        return self.app.cfg

    # The layout comes straight from the (validated) configuration.
    @property
    def split(self):
        return self.cfg["layout"] == "split"

    @property
    def view(self):  # single layout
        return self.cfg["view"]

    @property
    def split_top(self):
        return self.cfg["split_top"]

    @property
    def split_bottom(self):
        return self.cfg["split_bottom"]

    def active_views(self):
        if self.split:
            return [self.top_views[self.split_top], self.bottom_views[self.split_bottom]]
        return [self.views[self.view]]

    # ------------------------------------------------------------ window setup

    def _setup_window(self):
        # These are honoured by Mutter for X11 (XWayland) clients: no frame,
        # not in the dock / Alt-Tab / overview, on every workspace, and kept
        # underneath normal windows.
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_below(True)
        self.stick()
        self.set_resizable(False)
        self.get_style_context().add_class("deskcal")
        screen = self.get_screen()
        # Sub-pixel (LCD) anti-aliasing leaves coloured fringes on a translucent window.
        Gtk.Settings.get_for_screen(screen).set_property("gtk-xft-rgba", "none")
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        self.set_app_paintable(True)
        display = Gdk.Display.get_default()
        display.connect("monitor-added", lambda *a: GLib.timeout_add(500, self._place_once))
        display.connect("monitor-removed", lambda *a: GLib.timeout_add(500, self._place_once))
        screen.connect("size-changed", lambda *a: GLib.timeout_add(500, self._place_once))
        self.connect("configure-event", self._on_configure)
        self.connect("key-press-event", self._on_key)

    def _watch_resume(self):
        # Refresh shortly after waking from suspend.
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.signal_subscribe(
                "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE,
                lambda _c, _s, _p, _i, _n, params: (
                    None if params.unpack()[0] else GLib.timeout_add_seconds(8, self._refresh_once)))
        except GLib.Error:
            pass

    def target_monitor(self):
        display = Gdk.Display.get_default()
        monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
        primary = display.get_primary_monitor() or monitors[0]
        secondary = next((m for m in monitors if not m.is_primary()), primary)
        want = self.cfg["monitor"]
        if want == "primary":
            return primary
        for m in monitors:
            if m.get_model() == want:
                return m
        return secondary

    def place(self):
        if self.editing:
            return
        mon = self.target_monitor()
        wa = mon.get_workarea()
        anchor = self.cfg["anchor"]
        if anchor == "custom":
            geo = mon.get_geometry()
            w = min(int(self.cfg["width"]) or wa.width, wa.width)
            h = min(int(self.cfg["height"]) or wa.height, wa.height)
            x = min(max(geo.x + int(self.cfg["x"]), wa.x), wa.x + wa.width - w)
            y = min(max(geo.y + int(self.cfg["y"]), wa.y), wa.y + wa.height - h)
        else:
            margin = max(0, int(self.cfg["margin"]))
            max_w, max_h = wa.width - 2 * margin, wa.height - 2 * margin
            w = min(int(self.cfg["width"]) or max_w, max_w)
            h = min(int(self.cfg["height"]) or max_h, max_h)
            x = wa.x + margin if "left" in anchor else wa.x + wa.width - margin - w
            y = wa.y + margin if "top" in anchor else wa.y + wa.height - margin - h
            if anchor == "center":
                x, y = wa.x + (wa.width - w) // 2, wa.y + (wa.height - h) // 2
        self.set_size_request(w, h)
        self.resize(w, h)
        self.move(x, y)
        self._expected_pos = (x, y)
        self._snaps = 0

    def _place_once(self):
        self.place()
        return False

    def _on_configure(self, *_):
        if self.editing:
            self._update_edit_size()
        elif self._expected_pos and not self._snap_source:
            self._snap_source = GLib.timeout_add(600, self._snap_back)
        return False

    def _snap_back(self):
        # Locked: if something moved us (e.g. Super+drag), go back.
        self._snap_source = None
        if self.editing or not self._expected_pos:
            return False
        ex, ey = self._expected_pos
        x, y = self.get_position()
        if (abs(x - ex) > 4 or abs(y - ey) > 4) and self._snaps < 3:
            self._snaps += 1
            self.move(ex, ey)
        return False

    # ------------------------------------------------------------------ layout

    def _build(self):
        overlay = Gtk.Overlay()
        self.add(overlay)
        self.root = Gtk.EventBox()
        self.root.set_visible_window(False)
        self.root.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.root.connect("button-press-event", self._on_button_press)
        self.root.connect("motion-notify-event", self._on_motion)
        overlay.add(self.root)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        panel.get_style_context().add_class("panel")
        self.root.add(panel)

        # Clock and date
        self.clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.clock = label(classes=("clock",))
        self.date_lbl = label(classes=("date",))
        self.clock_box.pack_start(self.clock, False, False, 0)
        self.clock_box.pack_start(self.date_lbl, False, False, 0)
        panel.pack_start(self.clock_box, False, False, 0)

        # View switcher + navigation
        bar = Gtk.Box(spacing=4)
        seg = Gtk.Box()
        seg.get_style_context().add_class("seg")
        self.view_buttons = {}
        for key, text in VIEWS:
            btn = flat_button(Gtk.Label(label=text), classes=("segbtn",))
            btn.connect("clicked", lambda _b, k=key: self.set_view(k))
            btn.show_all()
            btn.set_no_show_all(True)  # Day / List are hidden in the split layout
            seg.pack_start(btn, False, False, 0)
            self.view_buttons[key] = btn
        bar.pack_start(seg, False, False, 0)
        next_btn = flat_button(icon="go-next-symbolic", tooltip="Next")
        next_btn.connect("clicked", lambda *_: self.navigate(1))
        prev_btn = flat_button(icon="go-previous-symbolic", tooltip="Previous")
        prev_btn.connect("clicked", lambda *_: self.navigate(-1))
        today_btn = flat_button(Gtk.Label(label="Today"), classes=("pill",), tooltip="Back to today")
        today_btn.connect("clicked", lambda *_: self.go_today())
        for w in (next_btn, prev_btn, today_btn):
            bar.pack_end(w, False, False, 0)
        panel.pack_start(bar, False, False, 0)

        self.title_lbl = label(classes=("view-title",), ellipsize=True)
        panel.pack_start(self.title_lbl, False, False, 0)

        # Views.  Single layout: one stack holding all four views.  Split layout:
        # a month/week stack above a day/list stack.  A widget cannot live in two
        # places, so the split layout has its own instances, fed by the same data.
        self.views = {"month": MonthView(self), "week": TimeGridView(self, 7),
                      "day": TimeGridView(self, 1), "list": ListView(self)}
        self.top_views = {"month": MonthView(self), "week": TimeGridView(self, 7)}
        self.bottom_views = {"day": TimeGridView(self, 1), "list": ListView(self)}
        self.stack = self._make_stack(self.views)
        self.top_stack = self._make_stack(self.top_views)
        self.bottom_stack = self._make_stack(self.bottom_views)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.single_clip = self._clip(self.stack)
        self.body.pack_start(self.single_clip, True, True, 0)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        # No F8 / arrow keys moving the divider either.  GtkPaned switches focus back on by
        # itself when its children become visible, so keep forcing it off.
        self.paned.set_can_focus(False)
        self.paned.connect("notify::can-focus", lambda p, _s: p.get_can_focus() and p.set_can_focus(False))
        lower = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lower.get_style_context().add_class("lower")
        day_head = Gtk.Box(spacing=2)
        self.day_title_lbl = label(classes=("day-title",), ellipsize=True)
        day_head.pack_start(self.day_title_lbl, True, True, 4)
        day_next = flat_button(icon="go-next-symbolic", tooltip="Next")
        day_next.connect("clicked", lambda *_: self.navigate_lower(1))
        day_prev = flat_button(icon="go-previous-symbolic", tooltip="Previous")
        day_prev.connect("clicked", lambda *_: self.navigate_lower(-1))
        day_head.pack_end(day_next, False, False, 0)
        day_head.pack_end(day_prev, False, False, 0)
        lower.pack_start(day_head, False, False, 0)
        lower.pack_start(self._clip(self.bottom_stack), True, True, 0)
        self.paned.pack1(self._clip(self.top_stack), True, True)
        self.paned.pack2(lower, True, True)
        # The divider follows the "top share" setting; it is not draggable, so
        # it stays put like the rest of the locked widget.
        self.paned.connect("realize", self._lock_divider)
        self.paned.connect("button-press-event", self._on_divider_press)
        self.paned.connect("size-allocate", self._on_paned_alloc)
        self.body.pack_start(self.paned, True, True, 0)
        self.body.show_all()
        self.single_clip.set_no_show_all(True)
        self.paned.set_no_show_all(True)
        panel.pack_start(self.body, True, True, 0)

        # Footer
        footer = Gtk.Box(spacing=2)
        self.status = label(classes=("status",), ellipsize=True)
        footer.pack_start(self.status, True, True, 4)
        refresh_btn = flat_button(icon="view-refresh-symbolic", tooltip="Refresh now")
        refresh_btn.connect("clicked", lambda *_: self.refresh(from_server=True))
        edit_btn = flat_button(icon="view-fullscreen-symbolic", tooltip="Move / resize the widget")
        edit_btn.connect("clicked", lambda *_: self.app.start_edit())
        settings_btn = flat_button(icon="emblem-system-symbolic", tooltip="Settings")
        settings_btn.connect("clicked", lambda *_: self.app.show_settings())
        for w in (refresh_btn, edit_btn, settings_btn):
            footer.pack_start(w, False, False, 0)
        panel.pack_start(footer, False, False, 0)

        # Edit-mode card (an overlay, so its buttons work while the panel is a drag area)
        self.editbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.editbar.get_style_context().add_class("editbar")
        self.editbar.set_halign(Gtk.Align.CENTER)
        self.editbar.set_valign(Gtk.Align.CENTER)
        self.editbar.pack_start(label("Move & resize", classes=("editbar-title",), xalign=0.5), False, False, 0)
        self.edit_size = label(classes=("editbar-size",), xalign=0.5)
        self.editbar.pack_start(self.edit_size, False, False, 0)
        hint = label("Drag anywhere to move.\nDrag an edge or corner to resize.\n"
                     "Enter = done · Esc = cancel", classes=("editbar-hint",), xalign=0.5)
        hint.set_justify(Gtk.Justification.CENTER)
        self.editbar.pack_start(hint, False, False, 0)
        buttons = Gtk.Box(spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        cancel = flat_button(Gtk.Label(label="Cancel"), classes=("pill",))
        cancel.connect("clicked", lambda *_: self.finish_edit(keep=False))
        done = flat_button(Gtk.Label(label="Done"), classes=("pill", "done"))
        done.connect("clicked", lambda *_: self.finish_edit(keep=True))
        buttons.pack_start(cancel, False, False, 0)
        buttons.pack_start(done, False, False, 0)
        self.editbar.pack_start(buttons, False, False, 4)
        self.editbar.show_all()
        self.editbar.set_no_show_all(True)
        self.editbar.hide()
        overlay.add_overlay(self.editbar)

    def apply_config(self, refetch=True):
        self.css.load_from_data(style.build_css(self.cfg["theme"], self.cfg["opacity"]).encode())
        self.colors = None
        self._update_color_css()
        self.clock_box.set_visible(bool(self.cfg["show_clock"]))
        self.clock_box.set_no_show_all(not self.cfg["show_clock"])
        self.place()
        self._tick(force=True)
        self.apply_layout()
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
        minutes = max(1, int(self.cfg["refresh_minutes"]))
        self._refresh_source = GLib.timeout_add_seconds(minutes * 60, self._refresh_timer)
        if refetch:
            self.fetched_range = None
            self.refresh()

    def _update_color_css(self):
        colors = {ev.color for ev in self.events}
        if colors != self.colors:
            self.colors = colors
            self.color_css.load_from_data(style.build_color_css(colors, self.cfg["theme"]).encode())

    # ------------------------------------------------------------ navigation

    def first_weekday(self):
        return 0 if self.cfg["week_starts_monday"] else 6

    def week_start(self, day):
        return day - timedelta(days=(day.weekday() - self.first_weekday()) % 7)

    @staticmethod
    def _make_stack(views):
        stack = Gtk.Stack()
        stack.set_hhomogeneous(False)  # size to the visible view only
        stack.set_vhomogeneous(False)
        # No crossfade: its last frame is painted through an offscreen copy, which
        # leaves the view a shade off on a translucent window.
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        for key, view in views.items():
            view.show_all()  # a Gtk.Stack refuses to switch to a page that is not shown yet
            stack.add_named(view, key)
        return stack

    @staticmethod
    def _clip(child):
        # Clip instead of growing: the widget keeps the size chosen in edit mode
        # whatever a view would like to have.
        clip = Gtk.ScrolledWindow()
        clip.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
        clip.add(child)
        return clip

    def _lock_divider(self, paned):
        handle = paned.get_handle_window()
        if handle is not None:
            handle.set_pass_through(True)  # the divider ignores the mouse

    @staticmethod
    def _on_divider_press(paned, event):
        # Belt and braces: a press that still lands on the divider must not start a drag.
        # (Presses bubbling up from the views have another event window and pass.)
        return event.window is not None and event.window == paned.get_handle_window()

    def _on_paned_alloc(self, _paned, alloc):
        if alloc.height != self._paned_h:
            self._paned_h = alloc.height
            GLib.idle_add(self._apply_ratio)

    @staticmethod
    def _ui_scale():
        """Font scaling (text-scaling-factor / HiDPI fonts): the fixed minimums grow with it."""
        dpi = Gtk.Settings.get_default().get_property("gtk-xft-dpi")  # 1024ths of a dpi, -1 if unset
        return max(1.0, dpi / 1024 / 96) if dpi and dpi > 0 else 1.0

    def _edit_min_size(self):
        return EDIT_MIN_SIZE[0], round((EDIT_MIN_HEIGHT_SPLIT if self.split else EDIT_MIN_SIZE[1]) * self._ui_scale())

    def _apply_ratio(self):
        h = self.paned.get_allocated_height()
        if h <= 1:
            h = self.body.get_allocated_height()  # just switched to split: the same room
        ratio = min(0.85, max(0.15, float(self.cfg["split_ratio"])))
        scale = self._ui_scale()
        top_min = round(TOP_MIN_HEIGHT[self.split_top] * scale)
        bottom_min = round(BOTTOM_MIN_HEIGHT * scale)
        key = (h, ratio, top_min)
        if h > 1 and key != self._ratio_key:
            self._ratio_key = key
            # The share wins, except that the lower pane keeps a usable strip and the
            # top view is never cut off (month rows would vanish) while there is room.
            pos = min(round(h * ratio), h - bottom_min)
            pos = max(pos, min(top_min, h - 60))
            if pos != self.paned.get_position():
                self.paned.set_position(pos)
        return False

    def apply_layout(self):
        """Show the layout and views chosen in the configuration."""
        split = self.split
        if self.editing:
            self.set_size_request(*self._edit_min_size())
        sig = (split, self.view, self.split_top, self.split_bottom)
        changed = sig != self._layout_sig
        self._layout_sig = sig
        self.single_clip.set_visible(not split)
        self.paned.set_visible(split)
        keys = config.TOP_VIEWS if split else config.SINGLE_VIEWS
        active = self.split_top if split else self.view
        for k, btn in self.view_buttons.items():
            btn.set_visible(k in keys)
            (btn.get_style_context().add_class if k == active
             else btn.get_style_context().remove_class)("active")
        self.stack.set_visible_child_name(self.view)
        self.top_stack.set_visible_child_name(self.split_top)
        self.bottom_stack.set_visible_child_name(self.split_bottom)
        views = self.active_views()
        for view in views:
            if view not in self._shown_views and isinstance(view, TimeGridView):
                view.want_scroll = True  # newly shown: scroll to now / the first event
        self._shown_views = views
        self._apply_ratio()
        self.render()
        if changed and self.fetched_range is not None:
            self._ensure_range()

    def _rescroll(self):
        """Scroll the visible time grids to 'now' / the first event on the next render.
        (A time grid also does this by itself whenever its own day or week changes.)"""
        for view in self.active_views():
            if isinstance(view, TimeGridView):
                view.want_scroll = True

    def set_view(self, key):
        """Toolbar buttons: pick the view (the top view in the split layout)."""
        cfg_key, allowed = (("split_top", config.TOP_VIEWS) if self.split
                            else ("view", config.SINGLE_VIEWS))
        if key not in allowed:
            return
        if self.cfg[cfg_key] != key:
            self.cfg[cfg_key] = key
            self.app.save_config_soon()
        self.apply_layout()
        if self.app.settings is not None:
            self.app.settings.sync_layout()

    def toggle_layout(self):
        self.cfg["layout"] = "single" if self.split else "split"
        self.app.save_config_soon()
        self.apply_layout()
        if self.app.settings is not None:
            self.app.settings.sync_layout()

    def _shift(self, view, delta):
        """Move the cursor by one step of `view` (a month, a day or a week)."""
        c = self.cursor
        if view == "month":
            y, m = divmod(c.month - 1 + delta, 12)
            y, m = c.year + y, m + 1
            return date(y, m, min(c.day, calendar.monthrange(y, m)[1]))
        return c + timedelta(days=delta if view == "day" else 7 * delta)

    def _moved(self):
        self.render()
        self._ensure_range()

    def navigate(self, delta):
        """Toolbar arrows: previous / next month, week or day (the top view in split)."""
        self.cursor = self._shift(self.split_top if self.split else self.view, delta)
        self._moved()

    def navigate_lower(self, delta):
        """Arrows of the lower pane in the split layout."""
        self.cursor = self._shift(self.split_bottom, delta)
        self._moved()

    def go_today(self):
        self.cursor = self.today
        self._rescroll()  # even when already on today: bring the day back to 'now'
        self._moved()

    def open_day(self, day):
        self.cursor = day
        if self.split:
            self._moved()  # the lower pane follows the selected day
        else:
            self.set_view("day")
            self._ensure_range()

    def on_scroll(self, _widget, event):
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            self._scroll_acc += event.delta_y
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            self._scroll_acc += -1 if event.direction == Gdk.ScrollDirection.UP else 1
        if abs(self._scroll_acc) >= 1:
            self.navigate(1 if self._scroll_acc > 0 else -1)
            self._scroll_acc = 0.0
        return True

    # ------------------------------------------------------------- edit mode

    def start_edit(self):
        if self.editing:
            return
        self.editing = True
        w, h = self.get_size()
        self._edit_backup = {k: self.cfg.get(k) for k in ("monitor", "anchor", "x", "y", "width", "height")}
        self.get_style_context().add_class("editing")
        self.root.set_above_child(True)  # every click on the panel becomes a drag
        self.editbar.show()
        self.set_keep_below(False)
        self.set_keep_above(True)  # visible over other windows while editing
        self.set_size_request(*self._edit_min_size())
        self.set_resizable(True)
        self.resize(w, h)
        self._update_edit_size()
        self.present()

    def finish_edit(self, keep=True):
        if not self.editing:
            return
        if keep:
            x, y = self.get_position()
            w, h = self.get_size()
            mon = Gdk.Display.get_default().get_monitor_at_point(x + w // 2, y + h // 2)
            geo = mon.get_geometry()
            if mon.get_model() != self.target_monitor().get_model():
                self.cfg["monitor"] = mon.get_model()
            self.cfg.update(anchor="custom", x=x - geo.x, y=y - geo.y, width=w, height=h)
        else:
            self.cfg.update(self._edit_backup)
        self.app.save_config_soon()
        self.editing = False
        self.get_style_context().remove_class("editing")
        self.root.set_above_child(False)
        self.editbar.hide()
        self._set_cursor(None)
        self.set_resizable(False)
        self.set_keep_above(False)
        self.set_keep_below(True)
        self.place()
        self.app.edit_finished()

    def _update_edit_size(self):
        w, h = self.get_size()
        self.edit_size.set_text(f"{w} × {h}")

    def _edge_at(self, x, y):
        w, h = self.get_size()
        m, c = 16, 36  # edge band / corner reach
        on_l, on_r, on_t, on_b = x < m, x > w - m, y < m, y > h - m
        near_l, near_r, near_t, near_b = x < c, x > w - c, y < c, y > h - c
        if (on_t and near_l) or (on_l and near_t):
            return E.NORTH_WEST
        if (on_t and near_r) or (on_r and near_t):
            return E.NORTH_EAST
        if (on_b and near_l) or (on_l and near_b):
            return E.SOUTH_WEST
        if (on_b and near_r) or (on_r and near_b):
            return E.SOUTH_EAST
        if on_t:
            return E.NORTH
        if on_b:
            return E.SOUTH
        if on_l:
            return E.WEST
        if on_r:
            return E.EAST
        return None

    def _set_cursor(self, name):
        if name == self._cursor_name or not self.get_window():
            return
        self._cursor_name = name
        cursor = Gdk.Cursor.new_from_name(self.get_display(), name) if name else None
        self.get_window().set_cursor(cursor)

    def _on_motion(self, _w, event):
        if self.editing:
            self._set_cursor(EDGE_CURSORS[self._edge_at(event.x, event.y)])
        return False

    def _on_key(self, _w, event):
        if not self.editing:
            return False
        if event.keyval == Gdk.KEY_Escape:
            self.finish_edit(keep=False)
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.finish_edit(keep=True)
            return True
        return False

    # ----------------------------------------------------------------- events

    def _on_button_press(self, _widget, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.button == 3:
            self._popup_menu(event)
            return True
        if self.editing and event.button == 1:
            edge = self._edge_at(event.x, event.y)
            rx, ry = int(event.x_root), int(event.y_root)
            if edge is None:
                self.begin_move_drag(event.button, rx, ry, event.time)
            else:
                self.begin_resize_drag(edge, event.button, rx, ry, event.time)
            return True
        return False

    def _popup_menu(self, event):
        if self.editing:
            items = [("Done", lambda *_: self.finish_edit(True)),
                     ("Cancel", lambda *_: self.finish_edit(False))]
        else:
            items = [("Refresh", lambda *_: self.refresh(from_server=True)),
                     ("Single view" if self.split else "Split view", lambda *_: self.toggle_layout()),
                     ("Move / resize…", lambda *_: self.app.start_edit()),
                     ("Settings…", lambda *_: self.app.show_settings()),
                     (None, None),
                     ("Quit", lambda *_: self.app.quit())]
        self.menu = Gtk.Menu()
        for text, cb in items:
            item = Gtk.SeparatorMenuItem() if text is None else Gtk.MenuItem(label=text)
            if cb:
                item.connect("activate", cb)
            self.menu.append(item)
        self.menu.attach_to_widget(self)
        self.menu.show_all()
        self.menu.popup_at_pointer(event)

    def _tick(self, force=False):
        now = datetime.now()
        if self.cfg["clock_24h"]:
            self.clock.set_text(now.strftime("%H:%M"))
        else:
            self.clock.set_text(now.strftime("%-I:%M %p"))
        self.date_lbl.set_text(now.strftime("%A, %-d %B %Y"))
        if now.date() != self.today:
            old, self.today = self.today, now.date()
            if self.cursor == old:
                self.cursor = self.today
            self.render()
            self.refresh()
        elif not force and now.minute != self._last_minute:
            self.render()
        self._last_minute = now.minute
        return True

    def _refresh_timer(self):
        self.refresh(from_server=True)
        return True

    def _refresh_once(self):
        self.refresh(from_server=True)
        return False

    # --------------------------------------------------------------- fetching

    def _needed_range(self):
        ranges = [view.visible_range() for view in self.active_views()]
        start, end = min(r[0] for r in ranges), max(r[1] for r in ranges)
        return min(start, self.today), max(end, self.today + timedelta(days=1))

    def _ensure_range(self):
        if self.fetching and self.fetched_range is None:
            return  # _on_fetched checks the range itself once the first fetch is in
        start, end = self._needed_range()
        have = self.fetched_range
        if have is None or start < have[0] or end > have[1]:
            if self._range_source:
                GLib.source_remove(self._range_source)
            self._range_source = GLib.timeout_add(300, self._range_refresh)

    def _range_refresh(self):
        self._range_source = None
        self.refresh()
        return False

    def refresh(self, from_server=False):
        if self.fetching:
            self.fetch_again = True
            return
        self.fetching = True
        start, end = self._needed_range()
        # Fetch a month either side so browsing does not refetch every click.
        start, end = start - timedelta(days=42), end + timedelta(days=42)
        cfg = dict(self.cfg)
        self.status.get_style_context().remove_class("error")
        self.status.set_text("Updating…")
        threading.Thread(target=self._worker, args=(cfg, start, end, from_server), daemon=True).start()

    def _worker(self, cfg, start, end, from_server):
        try:
            result = sources.fetch_all(cfg, start, end, refresh=from_server)
        except Exception as e:
            result = sources.FetchResult(errors=[sources.error_text(e)])
        GLib.idle_add(self._on_fetched, result, (start, end))

    def _on_fetched(self, result, rng):
        self.fetching = False
        self.events = result.events
        self.errors = result.errors
        self.notes = result.notes
        self.n_calendars = result.calendars
        self.fetched_range = rng
        self.last_update = datetime.now()
        self.by_day = {}
        for ev in self.events:
            ev.color = safe_color(ev.color)
            ev.cal_color = safe_color(ev.cal_color)
            for day in ev.days():
                if rng[0] <= day < rng[1]:
                    self.by_day.setdefault(day, []).append(ev)
        for evs in self.by_day.values():
            evs.sort(key=lambda e: (not e.all_day, e.start, e.title.lower()))
        self._update_color_css()
        self.render()
        if self.fetch_again:
            self.fetch_again = False
            self.refresh()
        else:
            self._ensure_range()
        # Right after login the network is often not up yet: retry soon, then
        # back off (90 s, 3 min, 6 min, ... up to the normal refresh interval).
        if not result.errors:
            self._retry_delay = 90
        elif not self._retry_source:
            self._retry_source = GLib.timeout_add_seconds(self._retry_delay, self._retry)
            self._retry_delay = min(self._retry_delay * 2, max(90, int(self.cfg["refresh_minutes"]) * 60))
        return False

    def _retry(self):
        self._retry_source = None
        self.refresh(from_server=True)
        return False

    # -------------------------------------------------------------- rendering

    def _popover_open(self):
        return self.popover is not None and self.popover.get_mapped()

    def _track_popover(self, pop):
        self.popover = pop
        pop.connect("closed", self._popover_closed)

    def _popover_closed(self, pop):
        if self.popover is pop:
            self.popover = None
        if self._render_pending:
            GLib.idle_add(self._flush_render)

    def _flush_render(self):
        if self._render_pending and not self._popover_open():
            self.render()
        return False

    def render(self):
        if self._popover_open():
            # Rebuilding the views now would destroy the widget the popover hangs on.
            self._render_pending = True
            return
        self._render_pending = False
        views = self.active_views()
        self.title_lbl.set_text(views[0].title())
        if self.split:
            self.day_title_lbl.set_text(views[1].title())
        for view in views:
            view.render()
        self._render_status()
        # Repaint everything, not just what changed: on a translucent window the
        # first paint and later partial repaints round colours a shade apart.
        self.queue_draw()

    def fmt(self, dt):
        return dt.strftime("%H:%M") if self.cfg["clock_24h"] else dt.strftime("%-I:%M %p").lower()

    def fmt_short(self, dt):
        if self.cfg["clock_24h"]:
            return dt.strftime("%H:%M")
        return dt.strftime("%-I:%M").replace(":00", "") + ("a" if dt.hour < 12 else "p")

    def when(self, ev, day):
        if ev.all_day:
            n = len(ev.days())
            return f"All day · {(day - ev.start.date()).days + 1}/{n}" if n > 1 else "All day"
        starts = ev.start >= local_midnight(day)
        ends = ev.end <= local_midnight(day + timedelta(days=1))
        if starts and ends:
            return self.fmt(ev.start) if ev.end == ev.start else f"{self.fmt(ev.start)} – {self.fmt(ev.end)}"
        if starts:
            return f"from {self.fmt(ev.start)}"
        if ends:
            return f"until {self.fmt(ev.end)}"
        return "All day"

    def _render_status(self):
        ctx = self.status.get_style_context()
        if self.fetching:
            return
        if self.errors:
            ctx.add_class("error")
            more = f" (+{len(self.errors) - 1})" if len(self.errors) > 1 else ""
            self.status.set_text("⚠ " + self.errors[0] + more)
            self.status.set_tooltip_text("\n".join(self.errors))
        else:
            ctx.remove_class("error")
            self.status.set_tooltip_text(None)
            if self.last_update:
                n = self.n_calendars
                text = f"Updated {self.fmt(self.last_update)} · {n} calendar{'s' if n != 1 else ''}"
                if self.notes:  # not an error: just something optional that is missing
                    text += " · " + self.notes[0]
                    self.status.set_tooltip_text("\n".join(self.notes))
                self.status.set_text(text)

    # ---------------------------------------------------------------- details

    def show_day_events(self, btn, day):
        """'+N more' of the day view: list the day's all-day events, each opens its details."""
        pop = Gtk.Popover(relative_to=btn)
        pop.set_position(Gtk.PositionType.TOP)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_border_width(8)
        for ev in self.by_day.get(day, []):
            if not covers_whole_day(ev, day):
                continue
            lbl = label(ev.title, ellipsize=True)
            lbl.set_max_width_chars(40)
            row = flat_button(lbl, classes=("chip", "allday", "c" + ev.color[1:]))
            row.connect("clicked", lambda _b, ev=ev: (pop.popdown(), self.show_event(btn, ev)))
            box.pack_start(row, False, False, 0)
        pop.add(box)
        box.show_all()
        pop.popup()
        self._track_popover(pop)

    def show_event(self, btn, ev):
        if self.editing:
            return
        pop = Gtk.Popover(relative_to=btn)
        pop.set_position(Gtk.PositionType.TOP)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(14)
        selectable_labels = []

        def add(markup, classes=(), selectable=False):
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(markup)
            lbl.set_line_wrap(True)
            lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_max_width_chars(46)
            if selectable:
                lbl.set_selectable(True)
                selectable_labels.append(lbl)
            for cls in classes:
                lbl.get_style_context().add_class(cls)
            box.pack_start(lbl, False, False, 0)

        own = f'<span foreground="{ev.color}">●</span> ' if ev.color != ev.cal_color else ""
        add(own + esc(ev.title), ("pop-title",))
        if ev.all_day:
            last = ev.days()[-1]
            when = ev.start.strftime("%A %-d %B")
            if last != ev.start.date():
                when += " – " + last.strftime("%A %-d %B")
        elif ev.end.date() != ev.start.date() and ev.end != local_midnight(ev.end.date()):
            when = (ev.start.strftime("%a %-d %b ") + self.fmt(ev.start) + " – "
                    + ev.end.strftime("%a %-d %b ") + self.fmt(ev.end))
        else:
            when = ev.start.strftime("%A %-d %B") + f" · {self.fmt(ev.start)} – {self.fmt(ev.end)}"
        add(esc(when), ("pop-meta",))
        add(f'<span foreground="{ev.cal_color}">●</span> {esc(ev.calendar)}', ("pop-meta",))
        if ev.location:
            add("📍 " + linkify(ev.location))
        if ev.url:
            add(linkify(ev.url))
        if ev.description:
            desc = ev.description if len(ev.description) < 1500 else ev.description[:1500] + "…"
            add(linkify(desc), ("pop-meta",), selectable=True)
        day_shown = (self.split_bottom if self.split else self.view) == "day"
        if not (day_shown and ev.start.date() == self.cursor):
            open_day = flat_button(Gtk.Label(label="Open day"), classes=("pill",))
            open_day.set_halign(Gtk.Align.START)
            open_day.connect("clicked", lambda *_: (pop.popdown(), self.open_day(ev.start.date())))
            box.pack_start(open_day, False, False, 4)
        pop.add(box)
        box.show_all()
        pop.popup()
        # A selectable label that receives focus selects all of its text.
        GLib.idle_add(lambda: [lbl.select_region(0, 0) for lbl in selectable_labels] and False)
        self._track_popover(pop)
