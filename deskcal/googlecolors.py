"""Per-event colours of Google Calendar events.

Google's CalDAV and iCal feeds carry no event colours; only the Calendar API does.
GNOME Online Accounts already holds the user's Google login and hands out its access
token over D-Bus, so for Google calendars DeskCal asks the API for the colour of each
event and gives the matching events that colour.

What is touched, on purpose as little as possible:
* the token stays in memory (never logged, never written to disk) and is only ever sent
  to https://www.googleapis.com;
* every request is a read-only GET with a field mask: for events only the event id, its
  start and its colour (no titles, attendees or descriptions), for the calendar only the
  ids and colours of its colour labels;
* any failure (offline, API refused, no login) just leaves the calendar's own colour,
  and a failing calendar is not asked again for a while.
"""

import re
import threading
import time
from datetime import date, datetime, timezone
from urllib.parse import quote

import requests
from gi.repository import Gio, GLib

from .colors import normalize_color

API = "https://www.googleapis.com/calendar/v3"
GOA = "org.gnome.OnlineAccounts"
EVENT_FIELDS = "nextPageToken,items(iCalUID,originalStartTime,start,colorId,eventLabelId)"
EVENT_FIELDS_NO_LABELS = "nextPageToken,items(iCalUID,originalStartTime,start,colorId)"
LABEL_FIELDS = "labelProperties(eventLabels(id,backgroundColor))"
MAX_PAGES = 20
LABELS_TTL = 3600         # seconds a calendar's colour labels are trusted ...
LABELS_MIN_REFETCH = 60   # ... unless an unknown label shows up (then at most this often)
BACKOFF_FIRST, BACKOFF_MAX = 60, 900  # seconds a failing calendar is left alone
TIMEOUT = (5, 15)         # connect, read

# The colours Google Calendar's own UI shows for the 11 classic event colour ids
# (Lavender ... Tomato).  The API's /colors endpoint still answers with an older palette.
MODERN_COLORS = {"1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73", "5": "#f6bf26", "6": "#f4511e",
                 "7": "#039be5", "8": "#616161", "9": "#3f51b5", "10": "#0b8043", "11": "#d50000"}


class GoogleColorError(Exception):
    """A readable reason why event colours are not available (never contains the token)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # HTTP status when Google answered


def instance_key(value):
    """The start of one event instance, in a form that iCal and the API agree on:
    UTC ISO text for date-times, ISO text for all-day dates."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.astimezone()  # floating time = local time
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return ""


def _api_key(when):
    if "dateTime" in when:
        return instance_key(datetime.fromisoformat(when["dateTime"]))
    return when.get("date", "")


def _api_error(response):
    try:
        err = response.json().get("error", {})
    except (ValueError, AttributeError):
        err = {}
    reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)} if isinstance(err, dict) else set()
    if "accessNotConfigured" in reasons or "SERVICE_DISABLED" in str(err):
        return "the Calendar API is not enabled for the Online Accounts Google app"
    if reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"}:
        return "Google rate limit reached, will retry later"
    if response.status_code in (401, 403):
        return "Google refused the Online Accounts login (HTTP %d)" % response.status_code
    return "Google Calendar API answered HTTP %d" % response.status_code


def _dbus_reason(error):
    """A short readable reason for a failed GetAccessToken call."""
    try:
        name = Gio.dbus_error_get_remote_error(error) or ""
        Gio.dbus_error_strip_remote_error(error)
    except Exception:
        name = ""
    if "ServiceUnknown" in name or "NameHasNoOwner" in name:
        return "Online Accounts is not running"
    if "NotAuthorized" in name:
        return "the Google login expired: sign in again in GNOME Settings → Online Accounts"
    first = (error.message.splitlines() or [""])[0]
    return "Online Accounts: " + first[:100]


class GoogleColors:
    def __init__(self):
        self._lock = threading.Lock()  # fetches of several calendars run in parallel
        self._bus = None
        self._tokens = {}         # account id -> (token, monotonic time it stops being usable)
        self._labels = {}         # calendar id -> (monotonic time fetched, {label id: colour})
        self._label_forced = {}   # calendar id -> monotonic time of the last refetch forced by an unknown label
        self._no_labels = False
        self._last = {}           # (account, calendar) -> last good {(uid, instance): colour or None}
        self._down = {}           # (account, calendar) -> (do not ask before, reason, current delay)

    def forget(self):
        """Drop the token and everything learnt (used when the feature is switched off)."""
        with self._lock:
            self._tokens.clear(); self._labels.clear(); self._label_forced.clear()
            self._last.clear(); self._down.clear()

    # ------------------------------------------------------------------ access

    def _token(self, account_id):
        if not re.fullmatch(r"[A-Za-z0-9_]+", account_id or ""):
            raise GoogleColorError("unexpected Online Accounts id")
        with self._lock:
            cached = self._tokens.get(account_id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            try:
                if self._bus is None:
                    self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                reply = self._bus.call_sync(
                    GOA, "/org/gnome/OnlineAccounts/Accounts/" + account_id, GOA + ".OAuth2Based",
                    "GetAccessToken", None, None, Gio.DBusCallFlags.NONE, 10000, None)
                token, expires = reply.unpack()
            except GLib.Error as e:
                raise GoogleColorError(_dbus_reason(e))
            except (AttributeError, TypeError, ValueError):
                raise GoogleColorError("Online Accounts gave no usable answer")
            self._tokens[account_id] = (token, time.monotonic() + max(0, expires - 120))
            return token

    def _get(self, account_id, path, params=None, _retry=True):
        r = requests.get(API + path, params=params, timeout=TIMEOUT,
                         headers={"Authorization": "Bearer " + self._token(account_id)})
        if r.status_code == 401 and _retry:
            with self._lock:
                self._tokens.pop(account_id, None)  # stale token: ask Online Accounts for a fresh one
            return self._get(account_id, path, params, _retry=False)
        if r.status_code != 200:
            raise GoogleColorError(_api_error(r), r.status_code)
        data = r.json()
        if not isinstance(data, dict):
            raise GoogleColorError("unexpected answer from Google")
        return data

    # ----------------------------------------------------------------- lookups

    def _label_colors(self, account_id, calendar_id, force=False):
        now = time.monotonic()
        with self._lock:
            cached = self._labels.get(calendar_id)
        if cached and not force and now - cached[0] < LABELS_TTL:
            return cached[1]
        labels = {}
        try:
            data = self._get(account_id, "/calendars/" + quote(calendar_id, safe=""), {"fields": LABEL_FIELDS})
            props = data.get("labelProperties")
            entries = props.get("eventLabels") if isinstance(props, dict) else None
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    labels[entry["id"]] = normalize_color(entry.get("backgroundColor"))
        except GoogleColorError as e:
            if e.status not in (400, 403, 404):  # e.g. a calendar shared read-only: no label info, classic colours still work
                raise
        with self._lock:
            self._labels[calendar_id] = (now, labels)
        return labels

    def _events(self, account_id, calendar_id, params):
        """All events of the window (a list of API items), following the pages."""
        path = "/calendars/%s/events" % quote(calendar_id, safe="")
        items, page_token = [], None
        for _ in range(MAX_PAGES):
            extra = {"pageToken": page_token} if page_token else {}
            if self._no_labels:
                page = self._get(account_id, path, dict(params, fields=EVENT_FIELDS_NO_LABELS, **extra))
            else:
                try:
                    page = self._get(account_id, path, dict(params, fields=EVENT_FIELDS, **extra))
                except GoogleColorError as e:
                    if e.status != 400 or page_token:
                        raise
                    # Google may one day reject the newer eventLabelId field.  Only if the same
                    # request without it works is that the reason: then keep the classic colours.
                    page = self._get(account_id, path, dict(params, fields=EVENT_FIELDS_NO_LABELS, **extra))
                    self._no_labels = True
            items += [i for i in page.get("items", []) if isinstance(i, dict)]
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return items

    # ------------------------------------------------------------------- public

    def colors_for(self, account_id, calendar_id, start, end):
        """({(iCalUID, instance start): '#rrggbb' or None}, problem or None) for one Google calendar.
        `None` marks an event Google reports without a colour.  `start` / `end` are the dates the
        caller is going to show.  On a problem the answer of the last successful run is returned."""
        key = (account_id, calendar_id)
        now = time.monotonic()
        with self._lock:
            down = self._down.get(key)
        if down and now < down[0]:
            return self._last.get(key, {}), down[1]  # asked recently, it failed: leave it alone for a while
        try:
            labels = {} if self._no_labels else self._label_colors(account_id, calendar_id)
            params = {"singleEvents": "true", "maxResults": 2500,
                      "timeMin": datetime.combine(start, datetime.min.time()).astimezone().isoformat(),
                      "timeMax": datetime.combine(end, datetime.min.time()).astimezone().isoformat()}
            items = self._events(account_id, calendar_id, params)
            unknown = {i.get("eventLabelId") for i in items if i.get("eventLabelId") and i.get("eventLabelId") not in labels}
            if unknown and not self._no_labels and now - self._label_forced.get(calendar_id, -1e9) > LABELS_MIN_REFETCH:
                self._label_forced[calendar_id] = now  # a label created or changed since we last looked
                labels = self._label_colors(account_id, calendar_id, force=True)
            mapping = {}
            for item in items:
                uid = item.get("iCalUID")
                if not isinstance(uid, str) or not uid:
                    continue
                color = labels.get(item.get("eventLabelId")) or MODERN_COLORS.get(item.get("colorId"))
                mapping[(uid, _api_key(item.get("originalStartTime") or item.get("start") or {}))] = color
            with self._lock:
                self._last[key] = mapping
                self._down.pop(key, None)
            return mapping, None
        except Exception as e:
            if isinstance(e, requests.RequestException):
                reason = "offline or Google unreachable"
            elif isinstance(e, GoogleColorError):
                reason = str(e)
            else:
                reason = "unexpected answer from Google"
            delay = min(down[2] * 2, BACKOFF_MAX) if down else BACKOFF_FIRST
            with self._lock:
                self._down[key] = (time.monotonic() + delay, reason, delay)
                return self._last.get(key, {}), reason


def apply(events, mapping):
    """Give events their Google colour: the exact instance if known.  For an instance the API
    did not list, the series colour, but only when every listed instance of the series has that
    same colour (an uncoloured sibling means the colour belongs to that one occurrence).
    Returns how many events were coloured."""
    by_uid = {}
    for (uid, _), color in mapping.items():
        by_uid.setdefault(uid, set()).add(color)
    n = 0
    for ev in events:
        key = (ev.uid, ev.instance)
        if key in mapping:
            color = mapping[key]
        else:
            seen = by_uid.get(ev.uid)
            color = next(iter(seen)) if seen and len(seen) == 1 else None
        if color:
            ev.color = color
            n += 1
    return n


_shared = None
_shared_lock = threading.Lock()


def shared():
    """One instance for the whole process, so tokens and labels are cached."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = GoogleColors()
        return _shared
