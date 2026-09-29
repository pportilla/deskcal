"""Calendar connectors.

* Online accounts: every calendar known to Evolution Data Server, i.e. the
  Google / Microsoft 365 / Nextcloud / CalDAV accounts added in
  GNOME Settings -> Online Accounts (and anything added in GNOME Calendar or
  Evolution).  Needs the gir1.2-ecal-2.0 and gir1.2-edataserver-1.2 packages.
* iCal links: any .ics URL, e.g. Google Calendar's "secret address in iCal
  format" or an Outlook "published calendar" link.

Both paths end up in parse_ics(), which expands recurrences with
recurring_ical_events and converts everything to local time.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi
import icalendar
import recurring_ical_events
import requests
from gi.repository import GLib

from . import config, googlecolors
from .colors import CSS_COLORS, normalize_color  # noqa: F401  (re-exported)

PALETTE = ["#3584e4", "#2ec27e", "#e66100", "#9141ac", "#e01b24",
           "#c64600", "#1c71d8", "#26a269", "#a347ba", "#986a44"]

@dataclass
class Event:
    title: str
    start: datetime  # aware, local time
    end: datetime    # aware, local time, exclusive
    all_day: bool
    calendar: str
    color: str  # what the event is drawn with: its own colour if it has one, else the calendar's
    location: str = ""
    description: str = ""
    url: str = ""
    cal_color: str = ""  # the calendar's colour
    uid: str = ""        # iCalendar UID and the start of this instance: how Google colours are matched
    instance: str = ""

    def __post_init__(self):
        if not self.cal_color:
            self.cal_color = self.color

    def days(self):
        """Every local date this event touches."""
        first = self.start.date()
        last = first
        if self.end > self.start:
            last = (self.end - timedelta(microseconds=1)).date()
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]


@dataclass
class CalendarInfo:
    uid: str
    name: str
    account: str
    color: str
    backend: str
    google: tuple = None  # (Online Accounts id, Google calendar id) of a Google calendar, else None


@dataclass
class FetchResult:
    events: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # something failed: worth retrying soon
    notes: list = field(default_factory=list)   # something optional is unavailable (Google event colours)
    calendars: int = 0


def error_text(e):
    if isinstance(e, GLib.Error):
        return e.message
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, requests.ConnectionError):
        return "offline or server unreachable"
    if isinstance(e, requests.Timeout):
        return "timed out"
    if isinstance(e, requests.RequestException):
        # requests puts the whole address into some messages, and a calendar's secret address must not show up on screen
        return "invalid link or network problem"
    return str(e) or type(e).__name__


# ---------------------------------------------------------------- iCal parsing

def _local(value):
    """date/datetime -> aware local datetime (naive/floating = local)."""
    if isinstance(value, datetime):
        return value.astimezone()
    return datetime.combine(value, time()).astimezone()


def _clean_text(value):
    text = str(value or "")
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&nbsp;", " ").replace("&amp;", "&").strip()


def parse_ics(data, cal_name, color, start, end):
    cal = icalendar.Calendar.from_ical(data)
    events = []
    for comp in recurring_ical_events.of(cal, skip_bad_series=True).between(start, end):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED" or "DTSTART" not in comp:
            continue
        dtstart = comp["DTSTART"].dt
        all_day = not isinstance(dtstart, datetime)
        if "DTEND" in comp:
            dtend = comp["DTEND"].dt
        elif "DURATION" in comp:
            dtend = dtstart + comp["DURATION"].dt
        else:
            dtend = dtstart + (timedelta(days=1) if all_day else timedelta(0))
        s, e = _local(dtstart), _local(dtend)
        rid = comp.get("RECURRENCE-ID")
        events.append(Event(
            uid=str(comp.get("UID", "")),
            instance=googlecolors.instance_key(rid.dt if rid is not None and hasattr(rid, "dt") else dtstart),
            title=str(comp.get("SUMMARY", "")).strip() or "(No title)",
            start=s, end=max(e, s), all_day=all_day,
            calendar=cal_name, color=normalize_color(comp.get("COLOR")) or color, cal_color=color,
            location=str(comp.get("LOCATION", "")).strip(),
            description=_clean_text(comp.get("DESCRIPTION")),
            url=str(comp.get("URL", "")).strip(),
        ))
    return events


def ics_calendar_meta(data):
    """(name, colour) a calendar feed announces about itself; '' where it says nothing."""
    try:
        cal = icalendar.Calendar.from_ical(data)
    except Exception:
        return "", ""
    color = next((c for c in (normalize_color(cal.get(k)) for k in
                              ("X-APPLE-CALENDAR-COLOR", "X-WR-CALCOLOR", "COLOR")) if c), "")
    return str(cal.get("X-WR-CALNAME", "")).strip(), color


# ------------------------------------------------------------------ iCal links

def download_ics(url):
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        return Path(unquote(parsed.path) if parsed.scheme else os.path.expanduser(url)).read_bytes()
    r = requests.get(url, timeout=30, headers={"User-Agent": "DeskCal/1.0"})
    r.raise_for_status()
    if b"BEGIN:VCALENDAR" not in r.content[:4096]:
        raise ValueError("not an iCal feed (use the iCal/.ics address, not the web page)")
    return r.content


def _fetch_feed(feed, start, end):
    """Returns (events, warning).  Falls back to the last good copy when offline."""
    name = feed.get("name") or "Calendar"
    url = config.lookup_feed_url(feed)
    if not url:
        raise ValueError("link not found in the keyring, add it again in Settings")
    cache = config.CACHE_DIR / f"{feed['id']}.ics"
    warning = None
    try:
        data = download_ics(url)
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception as e:
        if not cache.exists():
            raise
        data = cache.read_bytes()
        warning = f"{name}: {error_text(e)} (showing saved copy)"
    return parse_ics(data, name, normalize_color(feed.get("color")) or PALETTE[0], start, end), warning


# ------------------------------------------------ online accounts (EDS)

def _eds_modules():
    try:
        gi.require_version("EDataServer", "1.2")
        gi.require_version("ECal", "2.0")
        from gi.repository import ECal, EDataServer
    except (ValueError, ImportError):
        return None
    return EDataServer, ECal


def eds_available():
    return _eds_modules() is not None


def _google_link(registry, src, EDataServer):
    """(Online Accounts id, Google calendar id) when `src` is a Google calendar reached through a
    GNOME Online Accounts login (the login whose token can read event colours), else None."""
    try:
        if not src.has_extension("WebDAV Backend"):
            return None
        uri = src.get_extension("WebDAV Backend").dup_uri()
        if uri is None or uri.get_host() != "apidata.googleusercontent.com":
            return None
        parts = uri.get_path().strip("/").split("/")  # caldav/v2/<calendar id>/events
        parent = registry.ref_source(src.get_parent()) if src.get_parent() else None
        if len(parts) < 3 or parent is None or not parent.has_extension(EDataServer.SOURCE_EXTENSION_GOA):
            return None
        account = parent.get_extension(EDataServer.SOURCE_EXTENSION_GOA).get_account_id()
        return (account, unquote(parts[2])) if account else None
    except Exception:
        return None


def _eds_sources(registry, EDataServer):
    for src in registry.list_sources(EDataServer.SOURCE_EXTENSION_CALENDAR):
        if not registry.check_enabled(src):
            continue
        ext = src.get_extension(EDataServer.SOURCE_EXTENSION_CALENDAR)
        backend = ext.get_backend_name() or ""
        if backend == "weather":
            continue
        parent = registry.ref_source(src.get_parent()) if src.get_parent() else None
        info = CalendarInfo(
            uid=src.get_uid(), name=src.get_display_name(),
            account=parent.get_display_name() if parent else "",
            color=normalize_color(ext.get_color()) or PALETTE[0], backend=backend,
            google=_google_link(registry, src, EDataServer) if backend == "caldav" else None)
        yield info, src


def list_eds_calendars():
    mods = _eds_modules()
    if not mods:
        return []
    EDataServer, _ = mods
    registry = EDataServer.SourceRegistry.new_sync(None)
    return [info for info, _ in _eds_sources(registry, EDataServer)]


def _unwrap(result):
    # PyGObject returns (ok, value) for "gboolean f(..., out value, GError**)"
    return result[-1] if isinstance(result, tuple) else result


def _fetch_eds(ECal, src, info, start, end, refresh):
    client = ECal.Client.connect_sync(src, ECal.ClientSourceType.EVENTS, 30, None)
    if refresh and client.check_refresh_supported():
        try:
            client.refresh_sync(None)
        except GLib.Error:
            pass
    fmt = "%Y%m%dT%H%M%SZ"
    t0 = _local(start).astimezone(timezone.utc).strftime(fmt)
    t1 = _local(end).astimezone(timezone.utc).strftime(fmt)
    sexp = f'(occur-in-time-range? (make-time "{t0}") (make-time "{t1}"))'
    comps = _unwrap(client.get_object_list_sync(sexp, None)) or []

    parts, tzids = [], set()
    for comp in comps:
        text = comp.as_ical_string()
        parts.append(text if text.endswith("\n") else text + "\r\n")
        tzids.update(re.findall(r'TZID="?([^:;"\r\n]+)', text))
    zones = []
    for tzid in tzids:
        try:
            zone = _unwrap(client.get_timezone_sync(tzid, None))
            vtz = zone.get_component() if zone else None
            if vtz:
                text = vtz.as_ical_string()
                zones.append(text if text.endswith("\n") else text + "\r\n")
        except GLib.Error:
            pass  # icalendar still knows Olson names such as Europe/Madrid
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//DeskCal//EN\r\n"
           + "".join(zones) + "".join(parts) + "END:VCALENDAR\r\n")
    title = info.name
    if info.account and info.account != info.name and info.backend not in ("local", "contacts"):
        title += f" ({info.account})"  # tells apart calendars of different Google accounts
    return parse_ics(ics.encode(), title, info.color, start, end)


# ---------------------------------------------------------------------- main

def fetch_all(cfg, start: date, end: date, refresh=False):
    result = FetchResult()
    google_jobs, google_problems = [], set()
    google_on = cfg.get("google_event_colors", False)
    if not google_on:
        googlecolors.shared().forget()  # switched off: no token, nothing cached
    feeds = [f for f in cfg.get("ics", []) if f.get("enabled", True)]
    result.calendars += len(feeds)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [(f, pool.submit(_fetch_feed, f, start, end)) for f in feeds]

        mods = _eds_modules()
        if mods:
            EDataServer, ECal = mods
            hidden = set(cfg.get("eds_hidden", []))
            try:
                registry = EDataServer.SourceRegistry.new_sync(None)
                for info, src in _eds_sources(registry, EDataServer):
                    if info.uid in hidden:
                        continue
                    result.calendars += 1
                    try:
                        events = _fetch_eds(ECal, src, info, start, end, refresh)
                        if info.google and google_on:
                            # network, in parallel with everything else; colours are applied below
                            google_jobs.append((events, pool.submit(googlecolors.shared().colors_for, *info.google, start, end)))
                        result.events += events
                    except Exception as e:
                        result.errors.append(f"{info.name}: {error_text(e)}")
            except Exception as e:
                result.errors.append(f"Online accounts: {error_text(e)}")

        for events, fut in google_jobs:
            try:
                mapping, problem = fut.result()
                googlecolors.apply(events, mapping)
            except Exception:
                problem = "unexpected error"
            if problem:
                google_problems.add(problem)
        result.notes += [f"Google event colours unavailable: {p}" for p in sorted(google_problems)]

        for feed, fut in futures:
            try:
                events, warning = fut.result()
                result.events += events
                if warning:
                    result.errors.append(warning)
            except Exception as e:
                result.errors.append(f"{feed.get('name') or 'Calendar'}: {error_text(e)}")

    result.events.sort(key=lambda ev: (ev.start, not ev.all_day, ev.title.lower()))
    return result
