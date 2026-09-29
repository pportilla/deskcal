"""Month, week/day (time grid) and list views.

Every view gets the window as `host`, which provides the data (by_day,
today, cursor, cfg) and the shared actions (show_event, open_day, navigate).
Drawing is done with plain widgets + CSS, so no cairo bindings are needed.
"""

import re
from datetime import datetime, time, timedelta

from gi.repository import Gdk, GLib, Gtk, Pango

URL_RE = re.compile(r"https?://[^\s<>\"']+")
WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
GUTTER = 44  # hour labels column in the time grid


# ----------------------------------------------------------------- helpers

def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def linkify(text):
    out, pos = [], 0
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,);]")
        shown = url if len(url) <= 60 else url[:57] + "…"
        out.append(esc(text[pos:m.start()]))
        out.append(f'<a href="{esc(url).replace(chr(34), "&quot;")}">{esc(shown)}</a>')
        pos = m.start() + len(url)
    out.append(esc(text[pos:]))
    return "".join(out)


def local_midnight(day):
    return datetime.combine(day, time()).astimezone()


def wall_minutes(dt, day):
    """Minutes since midnight of `day` on the wall clock.  (Elapsed time would drift from
    the hour labels on the 23 / 25 hour days of a daylight-saving change.)"""
    d = dt.date()
    if d < day:
        return 0.0
    if d > day:
        return 1440.0
    return dt.hour * 60 + dt.minute + dt.second / 60


def flat_button(child=None, icon=None, tooltip=None, classes=()):
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_can_focus(False)
    ctx = btn.get_style_context()
    for cls in ("flat",) + tuple(classes):
        ctx.add_class(cls)
    if icon:
        btn.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
    elif child is not None:
        btn.add(child)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    return btn


def label(text="", classes=(), xalign=0.0, ellipsize=False):
    lbl = Gtk.Label(label=text, xalign=xalign)
    for cls in classes:
        lbl.get_style_context().add_class(cls)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    return lbl


def clear(container):
    for child in container.get_children():
        child.destroy()


def covers_whole_day(ev, day):
    return ev.all_day or (ev.start <= local_midnight(day)
                          and ev.end >= local_midnight(day + timedelta(days=1)))


def state_class(ev, now):
    if ev.all_day:
        return None
    if ev.start <= now < ev.end:
        return "now"
    if ev.end <= now:
        return "past"
    return None


def make_chip(host, ev, day, compact):
    """Small clickable event label used in month cells and the all-day row."""
    whole = covers_whole_day(ev, day)
    lbl = label(ellipsize=True)
    classes = ["chip", "c" + ev.color[1:]]
    if whole:
        lbl.set_text(ev.title)
        classes.append("allday")
    elif compact:
        lbl.set_text(ev.title)
        classes.append("tinted")
    else:
        when = host.fmt_short(ev.start) if ev.start >= local_midnight(day) else "…"
        lbl.set_markup(f'<span foreground="{ev.color}">●</span> <b>{esc(when)}</b> {esc(ev.title)}')
    btn = flat_button(lbl, classes=classes)
    state = state_class(ev, datetime.now().astimezone())
    if state == "past":
        btn.get_style_context().add_class("past")
    btn.set_tooltip_text(f"{ev.title}\n{host.when(ev, day)}")
    btn.connect("clicked", host.show_event, ev)
    return btn


# ------------------------------------------------------------------ month

class MonthView(Gtk.Box):
    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.host = host
        self.days = [None] * 42
        self.chips_per_cell = 0
        self.mode = "dots"  # dots | compact | full
        self._chip_time = None

        head = Gtk.Grid(column_homogeneous=True)
        self.head_lbls = []
        for col in range(7):
            lbl = label(classes=("weekday",), xalign=0.5)
            head.attach(lbl, col, 0, 1, 1)
            self.head_lbls.append(lbl)
        self.pack_start(head, False, False, 0)

        scroll_box = Gtk.EventBox()
        scroll_box.set_visible_window(False)
        scroll_box.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        scroll_box.connect("scroll-event", host.on_scroll)
        self.grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                             column_spacing=2, row_spacing=2)
        self.grid.set_vexpand(True)
        scroll_box.add(self.grid)
        self.pack_start(scroll_box, True, True, 0)

        self.cells = []
        for i in range(42):
            evbox = Gtk.EventBox()
            evbox.set_visible_window(False)
            evbox.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.get_style_context().add_class("mcell")
            num = label(classes=("num",), xalign=0.5)
            num.set_halign(Gtk.Align.CENTER)
            box.pack_start(num, False, False, 0)
            # How many chips fit is computed from the cell size (_on_alloc), and
            # the window clips its views, so a busy day cannot grow the widget.
            chips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.pack_start(chips, True, True, 0)
            evbox.add(box)
            evbox.connect("button-release-event", self._on_cell_release, i)
            evbox.connect("enter-notify-event", self._on_hover, box, True)
            evbox.connect("leave-notify-event", self._on_hover, box, False)
            self.grid.attach(evbox, i % 7, i // 7, 1, 1)
            self.cells.append((evbox, box, num, chips))
        self.grid.connect("size-allocate", self._on_alloc)

    def _on_hover(self, _w, event, box, inside):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        (box.get_style_context().add_class if inside else box.get_style_context().remove_class)("hover")
        return False

    def _on_cell_release(self, _w, event, index):
        if event.button != 1 or event.time == self._chip_time:
            return False
        self.host.open_day(self.days[index])
        return True

    def _chip_clicked(self, *_):
        self._chip_time = Gtk.get_current_event_time()

    def _measure(self):
        num_h, chip_h = 20, 17
        for _e, _b, num, chips in self.cells:
            if num.get_allocated_height() > 1:
                num_h = num.get_allocated_height()
            for child in chips.get_children():
                if isinstance(child, Gtk.Button) and child.get_allocated_height() > 1:
                    return num_h, child.get_allocated_height()
        return num_h, chip_h

    def _on_alloc(self, _grid, alloc):
        cell_w = (alloc.width - 12) / 7
        cell_h = (alloc.height - 10) / 6
        num_h, chip_h = self._measure()
        n = int((cell_h - num_h - 4) // (chip_h + 1))
        mode = "dots" if cell_w < 40 or n < 1 else ("compact" if cell_w < 92 else "full")
        if (n, mode) != (self.chips_per_cell, self.mode):
            self.chips_per_cell, self.mode = n, mode
            GLib.idle_add(self._render_idle)

    def _render_idle(self):
        self.render()
        return False

    def title(self):
        return self.host.cursor.strftime("%B %Y")

    def visible_range(self):
        start = self.host.week_start(self.host.cursor.replace(day=1))
        return start, start + timedelta(days=42)

    def render(self):
        host = self.host
        first = host.first_weekday()
        for col, lbl in enumerate(self.head_lbls):
            lbl.set_text(WEEKDAYS[(first + col) % 7])
        start, _ = self.visible_range()
        for i, (_evbox, box, num, chips) in enumerate(self.cells):
            day = start + timedelta(days=i)
            self.days[i] = day
            num.set_text(str(day.day))
            ctx = box.get_style_context()
            for cls, on in (("today", day == host.today),
                            ("selected", host.split and day == host.cursor and day != host.today),
                            ("other", day.month != host.cursor.month)):
                (ctx.add_class if on else ctx.remove_class)(cls)
            clear(chips)
            evs = host.by_day.get(day, [])
            n = len(evs)
            # (No tooltip on the cell itself: in GTK3 that makes the cell render
            # a shade darker than its neighbours on a translucent window.)
            if not evs:
                continue
            if self.mode == "dots":
                colors = []
                for ev in evs:
                    if ev.color not in colors:
                        colors.append(ev.color)
                dots = label(xalign=0.5, classes=("dots",))
                dots.set_markup(" ".join(f'<span foreground="{c}">●</span>' for c in colors[:4]))
                chips.pack_start(dots, False, False, 0)
            else:
                limit = self.chips_per_cell
                shown = evs if n <= limit else evs[:max(limit - 1, 0)]
                for ev in shown:
                    chip = make_chip(host, ev, day, self.mode == "compact")
                    chip.connect("clicked", self._chip_clicked)
                    chips.pack_start(chip, False, False, 0)
                if n > len(shown):
                    chips.pack_start(label(f"+{n - len(shown)} more", classes=("more",)), False, False, 0)
            chips.show_all()


# -------------------------------------------------------------- time grid

def _columns(segments):
    """Side-by-side layout for overlapping events: yields (seg, col, ncols)."""
    segments.sort(key=lambda s: (s[0], -s[1]))
    out, cluster, cluster_end = [], [], -1.0

    def flush():
        ends, placed = [], []
        for seg in cluster:
            for ci, end in enumerate(ends):
                if end <= seg[0]:
                    ends[ci] = seg[1]
                    placed.append((seg, ci))
                    break
            else:
                ends.append(seg[1])
                placed.append((seg, len(ends) - 1))
        out.extend((seg, ci, len(ends)) for seg, ci in placed)

    for seg in segments:
        if cluster and seg[0] >= cluster_end:
            flush()
            cluster, cluster_end = [], -1.0
        cluster.append(seg)
        cluster_end = max(cluster_end, seg[1])
    if cluster:
        flush()
    return out


class TimeGridView(Gtk.Box):
    """Week (7 columns) or day (1 column) timeline."""

    def __init__(self, host, n_days):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.host = host
        self.n = n_days
        self.hour_h = 42 if n_days > 1 else 52
        self.days = []
        self.width = 0
        self.want_scroll = True
        self._scroll_tries = 0
        self._range_key = None

        # Day headers (week only)
        self.head = Gtk.Box()
        spacer = Gtk.Box()
        spacer.set_size_request(GUTTER, -1)
        self.head.pack_start(spacer, False, False, 0)
        head_grid = Gtk.Grid(column_homogeneous=True)
        self.head.pack_start(head_grid, True, True, 0)
        self.head_btns = []
        for i in range(n_days):
            btn = flat_button(label(xalign=0.5, classes=("wk-day",), ellipsize=True), classes=("wk-head",),
                              tooltip="Open this day")
            btn.connect("clicked", lambda _b, i=i: host.open_day(self.days[i]))
            head_grid.attach(btn, i, 0, 1, 1)
            self.head_btns.append(btn)
        self.pack_start(self.head, False, False, 0)
        if n_days == 1:
            self.head.set_no_show_all(True)

        # All-day row
        self.allday = Gtk.Box()
        gl = label("all day", classes=("allday-label",), xalign=1)
        gl.set_size_request(GUTTER - 6, -1)
        gl.set_margin_end(6)
        self.allday.pack_start(gl, False, False, 0)
        allday_grid = Gtk.Grid(column_homogeneous=True, column_spacing=2)
        self.allday.pack_start(allday_grid, True, True, 0)
        self.allday_boxes = []
        for i in range(n_days):
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            allday_grid.attach(b, i, 0, 1, 1)
            self.allday_boxes.append(b)
        self.pack_start(self.allday, False, False, 0)

        # Hours
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        # Gtk.Layout scrolls itself (no Gtk.Viewport and its pixel cache).
        self.fixed = Gtk.Layout()
        self.fixed.set_size(1, 24 * self.hour_h + 8)
        self.scroller.add(self.fixed)
        self.scroller.connect("size-allocate", self._on_alloc)
        self.pack_start(self.scroller, True, True, 0)

    def _on_alloc(self, _w, alloc):
        if alloc.width != self.width:
            self.width = alloc.width
            GLib.idle_add(self._layout_idle)

    def _layout_idle(self):
        self._layout()
        return False

    def title(self):
        c = self.host.cursor
        if self.n == 1:
            prefix = "Today · " if c == self.host.today else ""
            return prefix + c.strftime("%A %-d %B %Y")
        s, e = self.visible_range()
        e -= timedelta(days=1)
        if s.month == e.month:
            return f"{s.day} – {e.day} {e:%B %Y}"
        return f"{s:%-d %b} – {e:%-d %b %Y}"

    def visible_range(self):
        start = self.host.cursor if self.n == 1 else self.host.week_start(self.host.cursor)
        return start, start + timedelta(days=self.n)

    def render(self):
        host = self.host
        start, _ = self.visible_range()
        if start != self._range_key:  # another day / week: scroll to its interesting hour
            self._range_key = start
            self.want_scroll = True
        self.days = [start + timedelta(days=i) for i in range(self.n)]
        for btn, day in zip(self.head_btns, self.days):
            lbl = btn.get_child()
            lbl.set_text(f"{day:%a} {day.day}")
            (lbl.get_style_context().add_class if day == host.today
             else lbl.get_style_context().remove_class)("today")
            # In the split layout the lower pane shows the selected day.
            (btn.get_style_context().add_class if host.split and day == host.cursor
             else btn.get_style_context().remove_class)("selected")

        any_allday = False
        for box, day in zip(self.allday_boxes, self.days):
            clear(box)
            whole = [ev for ev in host.by_day.get(day, []) if covers_whole_day(ev, day)]
            limit = 3 if self.n == 1 else 2
            shown = whole if len(whole) <= limit else whole[:limit - 1]
            for ev in shown:
                box.pack_start(make_chip(host, ev, day, True), False, False, 0)
            if len(whole) > len(shown):
                more = flat_button(label(f"+{len(whole) - len(shown)} more", classes=("more",)))
                more.connect("clicked", lambda b, d=day: host.open_day(d) if self.n > 1 else host.show_day_events(b, d))
                box.pack_start(more, False, False, 0)
            any_allday = any_allday or bool(whole)
            box.show_all()
        self.allday.set_visible(any_allday)
        self.allday.set_no_show_all(not any_allday)

        self._layout()
        if self.want_scroll:
            self.want_scroll = False
            self._scroll_tries = 0
            GLib.timeout_add(60, self._scroll_default)

    def _layout(self):
        host = self.host
        width = self.width - 2
        if width <= GUTTER + 20 or not self.days:
            return
        clear(self.fixed)
        colw = (width - GUTTER) // self.n
        H = self.hour_h
        self.fixed.set_size(width, 24 * H + 8)
        now = datetime.now().astimezone()

        for i, day in enumerate(self.days):
            if day == host.today and self.n > 1:
                tint = Gtk.Box()
                tint.get_style_context().add_class("todaycol")
                tint.set_size_request(colw, 24 * H)
                self.fixed.put(tint, GUTTER + i * colw, 0)
        for h in range(24):
            line = Gtk.Box()
            line.get_style_context().add_class("hourline")
            line.set_size_request(colw * self.n, 1)
            self.fixed.put(line, GUTTER, h * H)
            if h:
                text = f"{h:02d}:00" if host.cfg["clock_24h"] else f"{(h - 1) % 12 + 1} {'am' if h < 12 else 'pm'}"
                lbl = label(text, classes=("hourlabel",), xalign=1)
                lbl.set_size_request(GUTTER - 6, -1)
                self.fixed.put(lbl, 0, h * H - 7)
        for i in range(1, self.n):
            line = Gtk.Box()
            line.get_style_context().add_class("hourline")
            line.set_size_request(1, 24 * H)
            self.fixed.put(line, GUTTER + i * colw, 0)

        for i, day in enumerate(self.days):
            segments = []
            for ev in host.by_day.get(day, []):
                if covers_whole_day(ev, day):
                    continue
                sm = wall_minutes(ev.start, day)
                em = max(sm + 20, wall_minutes(ev.end, day))
                segments.append((sm, em, ev))
            for (sm, em, ev), col, ncols in _columns(segments):
                x = GUTTER + i * colw + 2 + int(col * (colw - 3) / ncols)
                w = max(int((colw - 3) / ncols) - 2, 8)
                y = int(sm * H / 60) + 1
                h = max(int((em - sm) * H / 60) - 2, 14)
                self.fixed.put(self._block(ev, day, w, h, now), x, y)

        if host.today in self.days:
            i = self.days.index(host.today)
            y = int(wall_minutes(now, host.today) * H / 60)
            line = Gtk.Box()
            line.get_style_context().add_class("nowline")
            line.set_size_request(colw, 2)
            self.fixed.put(line, GUTTER + i * colw, y)
            dot = Gtk.Box()
            dot.get_style_context().add_class("nowdot")
            dot.set_size_request(8, 8)
            self.fixed.put(dot, GUTTER + i * colw - 4, y - 3)
        self.fixed.show_all()

    def _block(self, ev, day, w, h, now):
        host = self.host
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_valign(Gtk.Align.START)
        box.pack_start(label(ev.title, classes=("block-title",), ellipsize=True), False, False, 0)
        when = host.when(ev, day)
        if h >= 30:
            meta = when
            if ev.location and (self.n == 1 or h >= 48):
                meta += " · " + ev.location.splitlines()[0]
            box.pack_start(label(meta, classes=("block-meta",), ellipsize=True), False, False, 0)
        if self.n == 1 and h >= 70 and ev.description:
            desc = label(ev.description.replace("\n", " ")[:300], classes=("block-meta",), ellipsize=True)
            box.pack_start(desc, False, False, 0)
        btn = flat_button(box, classes=("block", "c" + ev.color[1:]))
        btn.set_size_request(w, h)
        state = state_class(ev, now)
        if state:
            btn.get_style_context().add_class(state)
        btn.set_tooltip_text(f"{ev.title}\n{when}" + (f"\n{ev.location}" if ev.location else ""))
        btn.connect("clicked", host.show_event, ev)
        return btn

    def _scroll_default(self):
        adj = self.scroller.get_vadjustment()
        if not self.get_mapped() or adj.get_upper() <= adj.get_page_size() + 1:
            self._scroll_tries += 1  # not laid out yet (e.g. right at startup)
            return self._scroll_tries < 50
        H = self.hour_h
        if self.host.today in self.days:
            now = datetime.now()
            hours = now.hour + now.minute / 60 - 1.5
        else:
            starts = [ev.start.hour for d in self.days for ev in self.host.by_day.get(d, [])
                      if not covers_whole_day(ev, d) and ev.start.date() == d]
            hours = min(starts + [8]) - 0.5
        adj.set_value(max(0, min(hours * H, adj.get_upper() - adj.get_page_size())))
        return False


# ------------------------------------------------------------------- list

class ListView(Gtk.ScrolledWindow):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.add(self.box)
        self._start = None

    def title(self):
        c = self.host.cursor
        return "Upcoming" if c == self.host.today else c.strftime("From %A %-d %B")

    def visible_range(self):
        start = self.host.cursor
        return start, start + timedelta(days=max(1, int(self.host.cfg["agenda_days"])))

    def _day_header(self, day):
        host = self.host
        names = {host.today: "Today", host.today + timedelta(days=1): "Tomorrow",
                 host.today - timedelta(days=1): "Yesterday"}
        fmt = "%A %-d %B" if day in names else "%-d %B"
        if day.year != host.today.year:
            fmt += " %Y"
        box = Gtk.Box(spacing=8)
        name = label(names.get(day, day.strftime("%A")), classes=("day-name",))
        if day == host.today:
            name.get_style_context().add_class("today")
        box.pack_start(name, False, False, 0)
        box.pack_start(label(day.strftime(fmt), classes=("day-date",)), False, False, 0)
        btn = flat_button(box, classes=("day-header",), tooltip="Open this day")
        btn.connect("clicked", lambda *_: host.open_day(day))
        return btn

    def _row(self, ev, day, now):
        host = self.host
        box = Gtk.Box(spacing=10)
        bar = Gtk.Box()
        bar.set_size_request(4, -1)
        bar.get_style_context().add_class("bar")
        bar.get_style_context().add_class("c" + ev.color[1:])
        box.pack_start(bar, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.pack_start(label(ev.title, classes=("event-title",), ellipsize=True), False, False, 0)
        meta = host.when(ev, day)
        if ev.location:
            meta += " · " + ev.location.splitlines()[0]
        text.pack_start(label(meta, classes=("event-meta",), ellipsize=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        btn = flat_button(box, classes=("event",))
        state = state_class(ev, now)
        if state:
            btn.get_style_context().add_class(state)
        btn.connect("clicked", host.show_event, ev)
        return btn

    def render(self):
        host = self.host
        clear(self.box)
        now = datetime.now().astimezone()
        start, end = self.visible_range()
        days = (end - start).days
        shown = 0
        for i in range(days):
            day = start + timedelta(days=i)
            evs = host.by_day.get(day, [])
            if not evs and i > 0:
                continue
            self.box.pack_start(self._day_header(day), False, False, 0)
            if not evs:
                self.box.pack_start(label("Nothing scheduled", classes=("empty",)), False, False, 0)
            for ev in evs:
                self.box.pack_start(self._row(ev, day, now), False, False, 0)
                shown += 1
        if shown == 0 and days > 1:
            msg = (f"No events in the next {days} days." if host.n_calendars
                   else "No calendars yet. Right-click → Settings to connect your accounts.")
            self.box.pack_start(label(msg, classes=("empty",)), False, False, 0)
        self.box.show_all()
        if start != self._start:  # anchored at another day: show its top, not the old offset
            self._start = start
            self.get_vadjustment().set_value(0)
