"""Offline tests: colour parsing, per-event colours in iCal, feed metadata, config validation.

Run from the repository root:  python tests/test_colors_config.py
"""
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from deskcal import colors, config, sources, style  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"   [{detail}]" if detail else ""))


# ---- colour parsing
n = colors.normalize_color
cases = {
    "tomato": "#ff6347", "TOMATO": "#ff6347", " RebeccaPurple ": "#663399", '"turquoise"': "#40e0d0",
    "#3f51b5": "#3f51b5", "#F00": "#ff0000", "#f008": "#ff0000", "#FF2968FF": "#ff2968", "#9FE1E7FF": "#9fe1e7",
    "rgb(0,150,136)": "#009688", "rgb(0 150 136)": "#009688", "RGB( 10 , 20 , 30 )": "#0a141e",
    "rgba(10, 20, 30, 0.5)": "#0a141e", "rgba(10 20 30 / 50%)": "#0a141e", "rgb(300,0,0)": "#ff0000",
    "transparent": None, "bogus": None, "": None, None: None, "#12345": None, "rgb(1,2)": None, "rgb(1,2,3,4,5)": None,
    "url(x)": None, "red;background:url(x)": None, '#ff0000"><evil': None,
}
bad = {k: (n(k), v) for k, v in cases.items() if n(k) != v}
check("normalize_color: %d cases" % len(cases), not bad, bad)
check("normalize_color: 148 CSS colour names known", len(colors.CSS_COLORS) == 148, len(colors.CSS_COLORS))
t = time.time()
r = n("rgb(1" + " " * 300000 + "x")
check("normalize_color: hostile 300k-space value is rejected instantly", r is None and time.time() - t < 0.2, f"{(time.time() - t) * 1000:.1f} ms")

# ---- iCal: per-event COLOR and calendar-level colour
today = date.today() + timedelta(days=1)
ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nX-WR-CALNAME:Demo\r\nX-APPLE-CALENDAR-COLOR:#FF2968FF\r\n" +
       "".join(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:{uid}\r\nDTSTART;VALUE=DATE:{today:%Y%m%d}\r\n{extra}END:VEVENT\r\n"
               for uid, extra in (("red", "COLOR:tomato\r\n"), ("hex", "COLOR:#3f51b5\r\n"), ("bad", "COLOR:not-a-colour\r\n"), ("none", ""))) +
       "END:VCALENDAR\r\n").encode()
evs = {e.title: e for e in sources.parse_ics(ics, "Demo", "#2ec27e", today - timedelta(days=1), today + timedelta(days=2))}
check("parse_ics: an event's COLOR wins, its calendar colour is kept separately",
      evs["red"].color == "#ff6347" and evs["hex"].color == "#3f51b5" and all(e.cal_color == "#2ec27e" for e in evs.values()))
check("parse_ics: unusable or missing COLOR falls back to the calendar colour", evs["bad"].color == "#2ec27e" == evs["none"].color)
check("parse_ics: events carry UID and instance key", evs["red"].uid == "red" and evs["red"].instance == today.isoformat(), evs["red"].instance)
check("ics_calendar_meta: name and announced calendar colour", sources.ics_calendar_meta(ics) == ("Demo", "#ff2968"), sources.ics_calendar_meta(ics))

# ---- text colour on coloured chips
css = style.build_color_css({"#ff6347", "#000080", "#3584e4"}, "dark")
check("chip text: dark on tomato, white on navy and on the default blue",
      "chip.allday.cff6347 label { color: #1d1f24; }" in css and "chip.allday.c000080 label { color: #ffffff; }" in css
      and "chip.allday.c3584e4 label { color: #ffffff; }" in css)

# ---- error messages must not echo a calendar's secret address
import requests  # noqa: E402
secret = "https://calendar.example/ical/SECRET-TOKEN-123/basic.ics"
texts = [sources.error_text(requests.exceptions.InvalidURL(secret)), sources.error_text(requests.exceptions.MissingSchema(secret)),
         sources.error_text(requests.exceptions.TooManyRedirects(secret)), sources.error_text(requests.ConnectionError(secret)), sources.error_text(requests.Timeout(secret))]
check("error_text never echoes a pasted link", not any("SECRET-TOKEN" in t for t in texts), texts)

# ---- config loading and validation (in a temporary directory)
tmp = Path(tempfile.mkdtemp())
config.CONFIG_DIR, config.CONFIG_FILE = tmp, tmp / "config.json"


def load(text):
    config.CONFIG_FILE.write_text(text)
    return config.load()


c = load('{"layout": "split", }')
check("config: a syntax error is kept aside as config.json.bad-<date>, defaults are used",
      c["layout"] == "single" and any(p.startswith("config.json.bad-") for p in os.listdir(tmp)), sorted(os.listdir(tmp)))
c = load("[1, 2]")
check("config: a non-object top level is handled the same way", c["layout"] == "single")
c = load(json.dumps({"width": "abc", "height": None, "opacity": "x", "theme": 5, "anchor": "nowhere", "show_clock": "no",
                     "eds_hidden": "x", "monitor": 3, "agenda_days": 1000, "split_ratio": 9, "split_top": "day",
                     "ics": [1, {"id": 5}, {"id": "a", "color": 5, "name": 7, "enabled": "yes", "url": 3}, {"id": "b", "color": "#abc", "name": "N"}]}))
check("config: wrong types and out-of-range values fall back or are clamped",
      c["width"] == 620 and c["height"] == 0 and c["opacity"] == 0.86 and c["theme"] == "dark" and c["anchor"] == "top-right"
      and c["show_clock"] is True and c["eds_hidden"] == ["birthdays"] and c["monitor"] == "secondary" and c["agenda_days"] == 90
      and c["split_ratio"] == 0.85 and c["split_top"] == "month")
check("config: feed entries are cleaned (bad ones dropped, bad fields repaired)",
      [f["id"] for f in c["ics"]] == ["a", "b"] and c["ics"][0]["color"] == "#3584e4" and c["ics"][0]["enabled"] is True and "url" not in c["ics"][0])
c = load('{"width": Infinity, "opacity": NaN, "split_ratio": -Infinity}')
check("config: Infinity / NaN do not crash", c["width"] == 620 and c["opacity"] == 0.86 and c["split_ratio"] == 0.55, (c["width"], c["opacity"], c["split_ratio"]))
c = load(json.dumps({"width": 640, "view": "week"}))
check("config: a config from an older version (no new keys) gets the defaults",
      c["layout"] == "single" and c["split_bottom"] == "day" and c["google_event_colors"] is True and c["width"] == 640 and c["view"] == "week")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
