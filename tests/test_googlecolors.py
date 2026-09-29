"""Offline tests of deskcal.googlecolors with a fake Google API.  No token, no network, no D-Bus.

Run from the repository root:  python tests/test_googlecolors.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import time
from datetime import date, datetime, timezone
import requests
from gi.repository import Gio, GLib
from deskcal import googlecolors as gc
results = []
def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"   [{detail}]" if detail else ""))

LABELS = {"labelProperties": {"eventLabels": [{"id": "L1", "backgroundColor": "#f6bf26"}, {"id": "L2", "backgroundColor": "javascript:evil"}]}}
D = lambda s: {"dateTime": s}
def ev(uid, start, **kw):
    d = {"iCalUID": uid, "start": start}; d.update(kw); return d
ITEMS = [
    ev("a@g", D("2026-10-01T10:00:00+02:00"), colorId="5", eventLabelId="L1"),
    ev("b@g", D("2026-10-02T10:00:00+02:00"), colorId="10"),
    ev("c@g", {"date": "2026-10-03"}, colorId="3"),
    ev("r@g", D("2026-10-05T09:00:00Z"), originalStartTime=D("2026-10-05T09:00:00Z"), colorId="5"),
    ev("r@g", D("2026-10-12T09:00:00Z"), originalStartTime=D("2026-10-12T09:00:00Z"), colorId="5"),
    ev("m@g", D("2026-10-09T15:00:00Z"), originalStartTime=D("2026-10-08T09:00:00Z"), colorId="10"),
    ev("n@g", D("2026-10-04T10:00:00Z")),
    ev("x@g", D("2026-10-06T10:00:00Z"), colorId="99", eventLabelId="nope"),
    ev("bad@g", D("2026-10-07T10:00:00Z"), eventLabelId="L2"),
]
W = (date(2026, 9, 1), date(2026, 12, 1))
K = lambda *a: gc.instance_key(datetime(*a, tzinfo=timezone.utc))

class Fake(gc.GoogleColors):
    def __init__(self, pages=None, labels=LABELS, label_status=None):
        super().__init__(); self.calls = []; self.pages = pages or [ITEMS]; self.labels = labels; self.fail = None
        self.label_status = label_status; self.status_for = {}
    def _get(self, account_id, path, params=None, _retry=True):
        params = params or {}; self.calls.append((path, dict(params)))
        if self.fail: raise self.fail
        if "/events" in path:
            fields = params["fields"]
            if fields in self.status_for: raise gc.GoogleColorError("bad request", self.status_for[fields])
            i = int(params.get("pageToken", 0)); out = {"items": self.pages[i]}
            if i + 1 < len(self.pages): out["nextPageToken"] = str(i + 1)
            return out
        if self.label_status: raise gc.GoogleColorError("no", self.label_status)
        return self.labels
    def n(self, what): return sum(what in c[0] for c in self.calls)

f = Fake(); m, problem = f.colors_for("account_1", "cal@example.com", *W)
check("no problem reported", problem is None, problem)
check("label colour wins over the colour id", m[("a@g", K(2026, 10, 1, 8))] == "#f6bf26")
check("a colour id without label uses Google's UI colour (Basil / Grape), not the old API palette", m[("b@g", K(2026, 10, 2, 8))] == "#0b8043" and m[("c@g", "2026-10-03")] == "#8e24aa", (m[("b@g", K(2026, 10, 2, 8))], m[("c@g", "2026-10-03")]))
check("all 11 colour ids map to colours that are real Google label colours", set(gc.MODERN_COLORS) == {str(i) for i in range(1, 12)})
check("a moved recurring instance is keyed by its ORIGINAL start", ("m@g", K(2026, 10, 8, 9)) in m and ("m@g", K(2026, 10, 9, 15)) not in m)
check("uncoloured events / unknown ids / hostile label colours are recorded as 'no colour' (None)", m[("n@g", K(2026, 10, 4, 10))] is None and m[("x@g", K(2026, 10, 6, 10))] is None and m[("bad@g", K(2026, 10, 7, 10))] is None)
check("no separate /colors request any more", f.n("/colors") == 0)
check("events mask: only uid, start, original start, colour ids", [c[1]["fields"] for c in f.calls if "/events" in c[0]] == [gc.EVENT_FIELDS] and "summary" not in gc.EVENT_FIELDS and "attendees" not in gc.EVENT_FIELDS)
check("label mask is nested: only label ids and colours", {c[1]["fields"] for c in f.calls if "/events" not in c[0]} == {"labelProperties(eventLabels(id,backgroundColor))"})
check("singleEvents=true and timeMin/timeMax with offsets", all(c[1]["singleEvents"] == "true" and c[1]["timeMin"][10] == "T" for c in f.calls if "/events" in c[0]))

class E:
    def __init__(self, uid, inst): self.uid, self.instance, self.color = uid, inst, "#cal"
evs = [E("a@g", K(2026, 10, 1, 8)), E("r@g", K(2026, 10, 19, 9)), E("r@g", K(2026, 10, 5, 9)), E("m@g", K(2026, 10, 8, 9)), E("n@g", K(2026, 10, 4, 10)), E("zzz", "x"), E("c@g", "2026-10-03")]
n = gc.apply(evs, m)
check("apply: exact instances", evs[0].color == "#f6bf26" and evs[2].color == "#f6bf26" and evs[3].color == "#0b8043" and evs[6].color == "#8e24aa")
check("apply: unlisted instance gets the series colour when every listed instance agrees", evs[1].color == "#f6bf26")
check("apply: uncoloured and unknown events keep the calendar colour", evs[4].color == "#cal" and evs[5].color == "#cal")
check("apply: counts what it coloured", n == 5, n)

# the high-severity bug: ONE recoloured occurrence must not paint the whole series
series = {("rec@g", K(2026, 10, 27, 10)): "#d50000", ("rec@g", K(2026, 10, 20, 10)): None}
sib = [E("rec@g", K(2026, 10, 27, 10)), E("rec@g", K(2026, 10, 20, 10)), E("rec@g", K(2026, 11, 3, 10)), E("rec@g", K(2026, 11, 10, 10))]
gc.apply(sib, series)
check("ONE recoloured occurrence colours only that occurrence, not its siblings", [e.color for e in sib] == ["#d50000", "#cal", "#cal", "#cal"], [e.color for e in sib])
lone = {("rec@g", K(2026, 10, 27, 10)): "#d50000"}
sib2 = [E("rec@g", K(2026, 10, 27, 10)), E("rec@g", K(2026, 11, 3, 10))]; gc.apply(sib2, lone)
check("(series fallback still works for a series reported only as coloured)", [e.color for e in sib2] == ["#d50000", "#d50000"])

f2 = Fake(pages=[ITEMS[:3], ITEMS[3:6], ITEMS[6:]]); m2, _ = f2.colors_for("account_1", "cal@example.com", *W)
check("pagination: all pages are read", len(m2) == len(m) and f2.n("/events") == 3, len(m2))

# labels: cached, but an unknown label id (created / changed in Google) refetches, rate limited
f.calls.clear(); f.colors_for("account_1", "cal@example.com", *W)
check("labels are cached between refreshes when nothing is unknown to them", f.n("/events") == 1 and f.n("labelProperties") + sum("labelProperties" in c[1].get("fields", "") for c in f.calls) == 0, [c[0].split("/")[-1] for c in f.calls])
f3 = Fake(labels={"labelProperties": {"eventLabels": []}}); f3.colors_for("account_1", "c", *W)
check("an unknown label id triggers ONE label refetch", sum("labelProperties" in c[1].get("fields", "") for c in f3.calls) == 2, [c[0].split("/")[-1] for c in f3.calls])
f3.calls.clear(); f3.colors_for("account_1", "c", *W)
check("...but not on every refresh (rate limited)", sum("labelProperties" in c[1].get("fields", "") for c in f3.calls) == 0)
f3.labels = LABELS; f3._label_forced.clear(); f3.calls.clear(); m3, _ = f3.colors_for("account_1", "c", *W)
check("...and a label that appeared later is picked up", m3[("a@g", K(2026, 10, 1, 8))] == "#f6bf26")

# labels refused (shared / read-only calendar): classic colours survive
for status in (403, 404, 400):
    fl = Fake(label_status=status); ml, pl = fl.colors_for("a", "c", *W)
    check(f"calendar settings refused with {status}: colour ids still give colours, no problem shown", pl is None and ml[("b@g", K(2026, 10, 2, 8))] == "#0b8043", pl)
fl = Fake(label_status=500); ml, pl = fl.colors_for("a", "c", *W)
check("a server error on the label call IS a problem", pl is not None and ml == {}, pl)

# 400 handling on the events call
f5 = Fake(); f5.status_for = {gc.EVENT_FIELDS: 400}; m5, p5 = f5.colors_for("a", "c", *W)
check("400 on the label field only: falls back to classic colours and remembers", p5 is None and f5._no_labels and m5[("b@g", K(2026, 10, 2, 8))] == "#0b8043", f"no_labels={f5._no_labels}")
f6 = Fake(); f6.status_for = {gc.EVENT_FIELDS: 400, gc.EVENT_FIELDS_NO_LABELS: 400}; m6, p6 = f6.colors_for("a", "c", *W)
check("400 on BOTH requests is a plain failure and does NOT latch label colours off", p6 is not None and not f6._no_labels, f"no_labels={f6._no_labels}, {p6}")
f7 = Fake(); f7.status_for = {gc.EVENT_FIELDS: 500}; m7, p7 = f7.colors_for("a", "c", *W)
check("a 500 does not latch label colours off either", p7 is not None and not f7._no_labels)

# failure handling and backoff
f8 = Fake(); f8.colors_for("a", "c", *W); f8.fail = gc.GoogleColorError("Google refused the Online Accounts login (HTTP 403)", 403)
f8.calls.clear(); m8, p8 = f8.colors_for("a", "c", *W)
check("a failure keeps the last good colours and reports the reason", len(m8) == len(m) and "403" in p8, p8)
f8.calls.clear(); m8b, p8b = f8.colors_for("a", "c", *W)
check("a failing calendar is NOT asked again straight away (backoff), still reports the reason", f8.calls == [] and p8b == p8 and len(m8b) == len(m))
f8._down[("a", "c")] = (time.monotonic() - 1, p8, 60); f8.fail = None; m8c, p8c = f8.colors_for("a", "c", *W)
check("after the backoff it tries again and recovers", p8c is None and ("a", "c") not in f8._down)
f8.fail = requests.ConnectionError("boom"); f8._down.clear(); f8._labels.clear(); f8._last.clear(); f8.colors_for("a", "c", *W)
d1 = f8._down[("a", "c")][2]; f8._down[("a", "c")] = (time.monotonic() - 1, "x", d1); f8.colors_for("a", "c", *W)
check("backoff doubles up to a limit", f8._down[("a", "c")][2] == min(d1 * 2, gc.BACKOFF_MAX) and d1 == gc.BACKOFF_FIRST, (d1, f8._down[("a", "c")][2]))
f9 = Fake(); f9.fail = requests.ConnectionError("boom"); m9, p9 = f9.colors_for("a", "c", *W)
check("offline: empty result, readable reason, no exception", m9 == {} and "offline" in p9, p9)
f10 = Fake(); f10.labels = ["not", "a", "dict"]; m10, p10 = f10.colors_for("a", "c", *W)
check("a label answer of the wrong shape does not break colours", p10 is None or "unexpected" in p10, p10)
f10b = Fake(); f10b.labels = {"labelProperties": None}; m10b, p10b = f10b.colors_for("a", "c", *W)
check("labelProperties: null is tolerated", p10b is None and m10b[("b@g", K(2026, 10, 2, 8))] == "#0b8043", p10b)
f10c = Fake(pages=[["junk", 5, None] + ITEMS]); m10c, p10c = f10c.colors_for("a", "c", *W)
check("non-dict items in an events page are skipped", p10c is None and len(m10c) == len(m), p10c)
f11 = Fake(); f11.colors_for("a", "c", *W); f11.forget()
check("forget() drops tokens, labels and remembered colours", not f11._tokens and not f11._labels and not f11._last)

# error texts
class R:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self):
        if self._b is None: raise ValueError
        return self._b
check("error text: API not enabled", "not enabled" in gc._api_error(R(403, {"error": {"errors": [{"reason": "accessNotConfigured"}]}})))
check("error text: rate limit", "rate limit" in gc._api_error(R(403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}})))
check("error text: refusal / unknown / non-JSON / list body", "refused" in gc._api_error(R(401, {})) and "HTTP 500" in gc._api_error(R(500, None)) and "HTTP 500" in gc._api_error(R(500, [1])))
for name, expect in (("org.freedesktop.DBus.Error.ServiceUnknown", "not running"), ("org.gnome.OnlineAccounts.Error.NotAuthorized", "sign in again")):
    e = GLib.Error.new_literal(Gio.io_error_quark(), f"GDBus.Error:{name}: something", int(Gio.IOErrorEnum.DBUS_ERROR))
    check(f"D-Bus error {name.split('.')[-1]} becomes a readable hint", expect in gc._dbus_reason(e), gc._dbus_reason(e))

# _get: token handling, retry on 401, JSON shape, destinations
sent = []
class G(gc.GoogleColors):
    def __init__(self): super().__init__(); self.n = 0
    def _token(self, account_id): self.n += 1; return "T%d" % self.n
orig = requests.get
def run(answers):
    sent.clear()
    def fake_get(url, params=None, timeout=None, headers=None):
        sent.append((url, headers["Authorization"], timeout)); return answers.pop(0)
    requests.get = fake_get
    try:
        return G()
    finally:
        pass
try:
    g = run([R(401, {}), R(200, {"ok": 1})]); out = g._get("acc", "/x", {})
    check("401: a fresh token is requested once and the call retried", out == {"ok": 1} and [s[1] for s in sent] == ["Bearer T1", "Bearer T2"], [s[1] for s in sent])
    check("requests go only to www.googleapis.com over https, with connect+read timeouts", all(s[0].startswith("https://www.googleapis.com/calendar/v3/") and isinstance(s[2], tuple) for s in sent))
    g = run([R(200, ["a", "list"])])
    try: g._get("acc", "/x", {}); ok = False
    except gc.GoogleColorError as e: ok = "unexpected" in str(e)
    check("a JSON answer that is not an object is rejected cleanly", ok)
finally:
    requests.get = orig
for bad in ("../../etc", "account_é", "", None, "a b"):
    try: gc.GoogleColors()._token(bad); ok = False
    except gc.GoogleColorError: ok = True
    check(f"account id {bad!r} is refused before any D-Bus call", ok)
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
