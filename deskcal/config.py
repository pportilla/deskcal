"""Configuration file and keyring handling.

Settings live in ~/.config/deskcal/config.json.  iCal links are secrets
(Google's "secret address" grants read access to the calendar), so they are
stored in the GNOME keyring through libsecret whenever it is available.
"""

import copy
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

APP_ID = "io.github.deskcal.DeskCal"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "deskcal"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "deskcal"
CONFIG_FILE = CONFIG_DIR / "config.json"

ANCHORS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"]
SINGLE_VIEWS = ("month", "week", "day", "list")
TOP_VIEWS = ("month", "week")      # upper part of the split layout
BOTTOM_VIEWS = ("day", "list")     # lower part of the split layout

DEFAULTS = {
    # "secondary" picks the first non-primary monitor; otherwise a connector
    # name such as "DP-1" or "HDMI-1".
    "monitor": "secondary",
    # A preset from ANCHORS, or "custom" once the widget has been moved or
    # resized in edit mode (then x/y are relative to the monitor).
    "anchor": "top-right",
    "x": 24,
    "y": 24,
    "width": 620,
    "height": 0,  # 0 = use the full available height
    "margin": 24,
    "layout": "single",  # single | split
    "view": "month",  # single layout: month | week | day | list
    # Split layout: `split_top` above `split_bottom`; `split_ratio` is the share
    # of the height given to the top one.
    "split_top": "month",     # month | week
    "split_bottom": "day",    # day | list
    "split_ratio": 0.55,
    "opacity": 0.86,
    "theme": "dark",
    "agenda_days": 14,
    "refresh_minutes": 10,
    "week_starts_monday": True,
    "clock_24h": True,
    "show_clock": True,
    # Read each Google event's own colour from Google Calendar (through the Online Accounts login).
    "google_event_colors": True,
    # Calendars coming from GNOME Online Accounts / Evolution Data Server are
    # shown unless their source UID is listed here.
    "eds_hidden": ["birthdays"],
    # iCal feeds: {"id", "name", "color", "enabled"} (+ "url" only when the
    # keyring is unavailable).
    "ics": [],
}


def load():
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the top level must be an object")
        cfg.update(data)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        # Do not let the next save overwrite a file the user was editing (it holds the
        # calendar list): keep it aside and start from the defaults.
        bad = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.replace(CONFIG_FILE, bad)
            print(f"deskcal: could not read {CONFIG_FILE} ({e}); kept it as {bad}")
        except OSError:
            print(f"deskcal: could not read {CONFIG_FILE}: {e}")
    return sanitize(cfg)


# key -> (lowest, highest) for whole numbers
_INTS = {"x": (-30000, 30000), "y": (-30000, 30000), "width": (0, 30000), "height": (0, 30000),
         "margin": (0, 1000), "agenda_days": (1, 90), "refresh_minutes": (1, 1440)}
_BOOLS = ("week_starts_monday", "clock_24h", "show_clock", "google_event_colors")


def sanitize(cfg):
    """Fall back to the defaults for values a hand-edited file got wrong."""
    for key, allowed in (("layout", ("single", "split")), ("view", SINGLE_VIEWS),
                         ("split_top", TOP_VIEWS), ("split_bottom", BOTTOM_VIEWS),
                         ("theme", ("dark", "light")), ("anchor", ANCHORS + ["custom"])):
        if cfg.get(key) not in allowed:
            cfg[key] = DEFAULTS[key]
    for key, (lo, hi) in _INTS.items():
        try:
            cfg[key] = min(hi, max(lo, int(cfg[key])))
        except (TypeError, ValueError, OverflowError):  # OverflowError: Infinity
            cfg[key] = DEFAULTS[key]
    for key, (lo, hi) in (("split_ratio", (0.15, 0.85)), ("opacity", (0.0, 1.0))):
        try:
            value = float(cfg[key])
            if not math.isfinite(value):
                raise ValueError(value)
            cfg[key] = min(hi, max(lo, value))
        except (TypeError, ValueError, OverflowError):
            cfg[key] = DEFAULTS[key]
    for key in _BOOLS:
        if not isinstance(cfg.get(key), bool):
            cfg[key] = DEFAULTS[key]
    if not isinstance(cfg.get("monitor"), str):
        cfg["monitor"] = DEFAULTS["monitor"]
    hidden = cfg.get("eds_hidden")
    cfg["eds_hidden"] = [h for h in hidden if isinstance(h, str)] if isinstance(hidden, list) \
        else list(DEFAULTS["eds_hidden"])
    feeds = cfg.get("ics")
    cfg["ics"] = [f for f in map(_clean_feed, feeds) if f] if isinstance(feeds, list) else []
    return cfg


def _clean_feed(feed):
    if not isinstance(feed, dict) or not isinstance(feed.get("id"), str):
        return None
    feed = dict(feed)
    if not isinstance(feed.get("name"), str):
        feed["name"] = ""
    if not (isinstance(feed.get("color"), str) and re.fullmatch(r"#(?:[0-9a-fA-F]{3}){1,2}", feed["color"])):
        feed["color"] = "#3584e4"
    if not isinstance(feed.get("enabled"), bool):
        feed["enabled"] = True
    if "url" in feed and not isinstance(feed["url"], str):
        del feed["url"]
    return feed


def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def new_feed_id():
    return uuid.uuid4().hex[:12]


_SCHEMA = Secret.Schema.new(
    "io.github.deskcal.IcsFeed",
    Secret.SchemaFlags.NONE,
    {"feed_id": Secret.SchemaAttributeType.STRING},
)


def store_feed_url(feed, url):
    """Store a feed URL in the keyring; fall back to the (0600) config file."""
    try:
        ok = Secret.password_store_sync(
            _SCHEMA, {"feed_id": feed["id"]}, Secret.COLLECTION_DEFAULT,
            f"DeskCal calendar link: {feed.get('name') or feed['id']}", url, None)
        if ok:
            feed.pop("url", None)
            return
    except Exception as e:  # keyring locked, no secret service, ...
        print(f"deskcal: keyring unavailable, storing link in config: {e}")
    feed["url"] = url


def lookup_feed_url(feed):
    if feed.get("url"):
        return feed["url"]
    try:
        return Secret.password_lookup_sync(_SCHEMA, {"feed_id": feed["id"]}, None)
    except Exception as e:
        print(f"deskcal: keyring lookup failed: {e}")
        return None


def forget_feed_url(feed):
    try:
        Secret.password_clear_sync(_SCHEMA, {"feed_id": feed["id"]}, None)
    except Exception:
        pass
    cache = CACHE_DIR / f"{feed['id']}.ics"
    cache.unlink(missing_ok=True)
