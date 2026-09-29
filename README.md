# DeskCal

A calendar widget that lives on your desktop (by default on the secondary
monitor), below all other windows, showing your Google / Gmail, Microsoft 365,
Nextcloud, CalDAV and iCal calendars.

- **Month · Week · Day · List** views. Click a day to open it, click any event
  for its details (time, calendar, location, description, clickable Meet/Zoom
  links). Scroll over the month grid to change month.
- **Split layout**: a month or week on top and the selected day (or the list)
  below. Click a day in the top view and the bottom one shows it. Choose it in
  Settings → Position & appearance → *Layout*, or from the right-click menu.
- **Move / resize mode**: drag the widget anywhere, drag its edges or corners
  to resize, then *Done*. Afterwards it is locked in place (not resizable, kept
  below other windows, and it snaps back if something moves it).
- **Colours from your calendars**: each calendar keeps the colour it has in
  Online Accounts (or announces itself in its iCal feed), and an event that has
  its own colour is drawn in it (see *Colours* below).
- Dark / light theme, adjustable transparency, clock, 12/24 h, week start.
- Works offline with the last downloaded copy; refreshes periodically and
  after resume from suspend.

## Install

```bash
./install.sh              # or: ./install.sh --autostart  (start at login)
```

It installs into `~/.local/share/deskcal` with its own virtualenv and adds
*DeskCal* to the app grid. Uninstall with `./install.sh --uninstall`.

## Connecting calendars

**Option 1: GNOME Online Accounts (recommended for Gmail).**
Needs two small system packages once:

```bash
sudo apt install gir1.2-ecal-2.0 gir1.2-edataserver-1.2
```

Then *Settings → Online Accounts → Google* (repeat for every Gmail account;
Microsoft 365, Nextcloud and CalDAV work the same way) and keep *Calendar*
switched on. DeskCal picks them up automatically; untick the ones you don't
want in DeskCal's Settings → Calendars.

**Option 2: iCal links (no setup, read-only).**
DeskCal Settings → Calendars → *Add calendar link…*

- Google: calendar.google.com → ⚙ Settings → your calendar → *Integrate
  calendar* → *Secret address in iCal format*.
- Outlook / Microsoft 365: Settings → Calendar → Shared calendars → *Publish a
  calendar* → ICS link.
- Nextcloud, iCloud, Fastmail…: the calendar's subscription (.ics / webcal) link.

Links are stored in the GNOME keyring, not in the config file.

## Colours

- The calendar colour comes from Online Accounts (Google, Nextcloud, ...). For
  an iCal link the colour the feed announces for itself (`X-APPLE-CALENDAR-COLOR`,
  `X-WR-CALCOLOR`, `COLOR`) is preselected when you add it; pick another one to
  override it.
- Events that carry their own colour (the standard iCalendar `COLOR` property, as
  written by Nextcloud, Evolution, KOrganizer, DAVx5, ...) are drawn in it: month
  dots and chips, week and day blocks, all-day bars. The popover shows a dot in the
  event's colour before the title and one in the calendar's colour before its name.
  Names (`turquoise`), `#rgb` / `#rrggbb` / `#rrggbbaa` and `rgb(...)` are understood.
- **Google events**: Google's CalDAV and iCal feeds carry no event colours (Tomato, Sage,
  Peacock, ...), only the Google Calendar API does. For a Google account added in
  Online Accounts, DeskCal therefore asks that API for each event's colour and draws the
  event in it (switch: Settings → Calendars → *Google event colours*). Events you never
  coloured in Google keep the calendar's colour.
  What this touches: it uses the login GNOME Online Accounts already holds (the token is
  fetched over D-Bus, kept in memory only, and sent only to `www.googleapis.com`), makes
  read-only requests, and asks for nothing but each event's id, start time and colour plus
  the ids and colours of the calendar's colour labels: no titles, attendees, descriptions
  or label names. If Google refuses or you are offline, events simply keep the calendar
  colour, the status bar says why (as a note, not an error) and that calendar is left alone
  for a while before DeskCal tries again. Google accounts that are not
  added through Online Accounts (Evolution's own login) are not covered.

## Using it

- Right-click the widget: Refresh, Split view / Single view, Move / resize…,
  Settings…, Quit.
- Split layout: the toolbar (Month / Week, arrows, Today) drives the top view;
  the lower pane has its own arrows (a day at a time, or a week in the list).
  The selected day is outlined in the top view. Settings lets you pick the top
  view (Month / Week), the bottom view (Day / List) and how much of the height
  the top gets (*Top share*; the top view never shrinks below what it needs to
  stay readable, and the bottom keeps a usable strip). The divider itself is
  fixed, like the rest of the locked widget.
- A day with many all-day events shows *+N more*: click it for the list.
- Footer buttons: refresh, move / resize, settings.
- Running `deskcal` again while it runs opens Settings; `deskcal --edit`
  enters move / resize mode; `deskcal --quit` stops it. The same actions are
  in the app grid icon's right-click menu.
- Settings are in `~/.config/deskcal/config.json`. If that file is ever
  unreadable (say a typo while editing it by hand), DeskCal keeps it as
  `config.json.bad-<date>` and starts from the defaults instead of overwriting it.

## Notes

GNOME on Wayland doesn't let native Wayland windows pick their own monitor or
position, or stay below other windows. DeskCal therefore runs through
XWayland, where Mutter honours all of that. Apps it launches (browser, GNOME
Settings) still run natively.

## Development

```bash
python3 -m venv --system-site-packages .venv      # sees the system PyGObject / GTK 3
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python -m deskcal          # run from the source tree
.venv/bin/python tests/test_colors_config.py      # offline tests: colours, iCal, config
.venv/bin/python tests/test_googlecolors.py       # offline tests of the Google module (fake API)
```

The tests need no display, account, token or network. Layout, drawing and the
Google connection were additionally checked by hand on a live GNOME session.

Requires GTK 3 with PyGObject (`python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-secret-1`) and,
for Online Accounts calendars, `gir1.2-ecal-2.0` and `gir1.2-edataserver-1.2`.

## Licence

MIT, see [LICENSE](LICENSE).
