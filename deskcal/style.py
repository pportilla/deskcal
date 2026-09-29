"""GTK3 CSS for the widget, generated from the theme settings."""

THEMES = {
    "dark": {
        "bg": (22, 24, 30), "fg": "#eceff4", "dim": "rgba(236,239,244,0.58)",
        "faint": "rgba(236,239,244,0.30)", "hover": "rgba(255,255,255,0.07)",
        "line": "rgba(255,255,255,0.09)", "accent": "#62a0ea", "accent_rgb": (98, 160, 234),
        "on_accent": "#0b1a2e", "error": "#ff7b72", "nowline": "#ff6b6b", "tint": 0.30,
    },
    "light": {
        "bg": (250, 250, 252), "fg": "#1d1f24", "dim": "rgba(29,31,36,0.60)",
        "faint": "rgba(29,31,36,0.32)", "hover": "rgba(0,0,0,0.06)",
        "line": "rgba(0,0,0,0.09)", "accent": "#1c71d8", "accent_rgb": (28, 113, 216),
        "on_accent": "#ffffff", "error": "#c01c28", "nowline": "#e01b24", "tint": 0.20,
    },
}


def build_css(theme, opacity):
    t = THEMES.get(theme, THEMES["dark"])
    r, g, b = t["bg"]
    ar, ag, ab = t["accent_rgb"]
    # Snap to an exact 8-bit alpha: otherwise GTK's first paint and later partial
    # repaints round differently and redrawn areas show up a shade off.
    a = round(max(0.0, min(1.0, float(opacity))) * 255) / 255
    return f"""
window.deskcal, window.deskcal.background {{ background-color: transparent; }}
.deskcal .panel {{
    background-color: rgba({r},{g},{b},{a:.6f});
    border: 1px solid {t['line']};
    border-radius: 22px;
    padding: 18px 16px 10px 16px;
}}
.deskcal.editing .panel {{ border: 2px dashed {t['accent']}; }}
.deskcal label {{ color: {t['fg']}; }}
.deskcal .clock {{ font-size: 38pt; font-weight: 300; }}
.deskcal .date {{ font-size: 12pt; color: {t['dim']}; }}
.deskcal .view-title {{ font-size: 13pt; font-weight: bold; margin: 0 4px; }}
.deskcal .day-title {{ font-size: 11.5pt; font-weight: bold; }}
.deskcal paned > separator {{ background-color: transparent; background-image: none; min-height: 1px; }}
.deskcal .lower {{ border-top: 1px solid {t['line']}; padding-top: 6px; margin-top: 6px; }}
.deskcal .weekday {{ font-size: 8pt; font-weight: bold; color: {t['dim']}; padding: 2px 0 4px 0; }}
.deskcal viewport, .deskcal scrolledwindow, .deskcal layout {{ background: transparent; border: none; }}
.deskcal undershoot, .deskcal overshoot {{ background: none; border: none; box-shadow: none; }}

.deskcal button.flat {{
    background: none; border: none; box-shadow: none; outline: none;
    border-radius: 10px; padding: 4px 6px; min-height: 0; min-width: 0;
    color: {t['fg']}; -gtk-icon-shadow: none; text-shadow: none;
}}
.deskcal button.flat:hover {{ background-color: {t['hover']}; }}
.deskcal button.flat image {{ color: {t['dim']}; }}
.deskcal button.flat:hover image {{ color: {t['fg']}; }}
.deskcal button.pill {{ border: 1px solid {t['line']}; border-radius: 99px; padding: 2px 10px; }}
.deskcal button.pill label {{ font-size: 9pt; }}
.deskcal button.pill.done {{ background-color: {t['accent']}; border-color: {t['accent']}; }}
.deskcal button.pill.done label {{ color: {t['on_accent']}; font-weight: bold; }}

.deskcal .seg {{ background-color: {t['hover']}; border-radius: 99px; padding: 2px; }}
.deskcal button.segbtn {{ border-radius: 99px; padding: 2px 10px; }}
.deskcal button.segbtn label {{ font-size: 9pt; color: {t['dim']}; }}
.deskcal button.segbtn.active {{ background-color: {t['accent']}; }}
.deskcal button.segbtn.active label {{ color: {t['on_accent']}; font-weight: bold; }}

/* month */
.deskcal .mcell {{ border-radius: 8px; padding: 2px 2px 0 2px; }}
.deskcal .mcell.hover {{ background-color: {t['hover']}; }}
.deskcal .mcell.selected {{ box-shadow: inset 0 0 0 2px {t['accent']}; }}
.deskcal .mcell label.num {{ font-size: 9.5pt; padding: 0 5px; border-radius: 99px; }}
.deskcal .mcell.other label.num {{ color: {t['faint']}; }}
.deskcal .mcell.today label.num {{ background-color: {t['accent']}; color: {t['on_accent']}; font-weight: bold; }}
.deskcal .dots {{ font-size: 6pt; }}
.deskcal button.chip {{ padding: 0 4px; border-radius: 5px; }}
.deskcal button.chip label {{ font-size: 8pt; }}
.deskcal button.chip.past {{ opacity: 0.5; }}
.deskcal .more {{ font-size: 7.5pt; color: {t['dim']}; padding-left: 4px; }}

/* week / day */
.deskcal button.wk-head {{ padding: 2px; }}
.deskcal button.wk-head.selected {{ box-shadow: inset 0 0 0 2px {t['accent']}; }}
.deskcal .wk-day {{ font-size: 9pt; font-weight: bold; color: {t['dim']}; }}
.deskcal .wk-day.today {{ color: {t['accent']}; }}
.deskcal .allday-label {{ font-size: 7pt; color: {t['faint']}; }}
.deskcal .hourline {{ background-color: {t['line']}; }}
.deskcal .hourlabel {{ font-size: 7.5pt; color: {t['faint']}; }}
.deskcal .todaycol {{ background-color: rgba({ar},{ag},{ab},0.06); }}
.deskcal .nowline {{ background-color: {t['nowline']}; }}
.deskcal .nowdot {{ background-color: {t['nowline']}; border-radius: 99px; }}
.deskcal button.block {{ border-radius: 6px; padding: 2px 5px; }}
.deskcal button.block.past {{ opacity: 0.5; }}
.deskcal .block-title {{ font-size: 8.5pt; font-weight: bold; }}
.deskcal .block-meta {{ font-size: 7.5pt; color: {t['dim']}; }}

/* list */
.deskcal separator {{ background-color: {t['line']}; min-height: 1px; }}
.deskcal .day-name {{ font-size: 10.5pt; font-weight: bold; }}
.deskcal .day-name.today {{ color: {t['accent']}; }}
.deskcal .day-date {{ font-size: 9.5pt; color: {t['dim']}; }}
.deskcal button.day-header {{ margin-top: 8px; padding: 2px 6px; }}
.deskcal button.event {{ padding: 5px 6px; border-radius: 10px; }}
.deskcal .bar {{ min-width: 4px; border-radius: 2px; background-color: {t['accent']}; }}
.deskcal button.event.now {{ background-color: rgba({ar},{ag},{ab},0.16); }}
.deskcal button.event.past {{ opacity: 0.45; }}
.deskcal .event-title {{ font-size: 10.5pt; }}
.deskcal .event-meta {{ font-size: 8.5pt; color: {t['dim']}; }}
.deskcal .empty {{ color: {t['dim']}; font-style: italic; padding: 8px 6px; }}

.deskcal .status {{ font-size: 8pt; color: {t['faint']}; }}
.deskcal .status.error {{ color: {t['error']}; }}
.deskcal scrollbar, .deskcal scrollbar trough {{ background: transparent; border: none; }}
.deskcal scrollbar slider {{ background-color: {t['faint']}; border: none; min-width: 4px; }}

/* edit mode card */
.deskcal .editbar {{
    background-color: rgba({r},{g},{b},0.97); border: 1px solid {t['accent']};
    border-radius: 16px; padding: 14px 20px;
}}
.deskcal .editbar-title {{ font-weight: bold; font-size: 11pt; }}
.deskcal .editbar-size {{ font-size: 20pt; font-weight: 300; }}
.deskcal .editbar-hint {{ font-size: 9pt; color: {t['dim']}; }}

.deskcal popover, .deskcal popover.background {{
    background-color: rgb({r},{g},{b}); border: 1px solid {t['line']}; border-radius: 14px;
}}
.deskcal popover .pop-title {{ font-size: 12pt; font-weight: bold; }}
.deskcal popover .pop-meta {{ color: {t['dim']}; }}
.deskcal popover label link {{ color: {t['accent']}; }}
"""


def _linear(c):
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(r, g, b):
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


DARK_TEXT = "#1d1f24"


def _text_on(r, g, b):
    """White text (the usual look) unless it would be hard to read on this colour, then dark text.
    3.7:1 keeps the default blue white while tomato, yellow, orange, ... get dark text."""
    white = 1.05 / (_luminance(r, g, b) + 0.05)
    return "#ffffff" if white >= 3.7 else DARK_TEXT


def _rgb(color):
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def build_color_css(colors, theme):
    """Per-calendar colour classes: .c<hex> on bars, chips and time blocks."""
    tint = THEMES.get(theme, THEMES["dark"])["tint"]
    rules = []
    for color in sorted(colors):
        r, g, b = _rgb(color)
        k = "c" + color[1:]
        text = _text_on(r, g, b)
        rules.append(
            f".deskcal .bar.{k}, .deskcal button.chip.allday.{k} {{ background-color: {color}; }}\n"
            f".deskcal button.chip.allday.{k} label {{ color: {text}; }}\n"
            f".deskcal button.chip.tinted.{k} {{ background-color: rgba({r},{g},{b},{tint}); "
            f"border-left: 3px solid {color}; border-radius: 3px; }}\n"
            f".deskcal button.block.{k} {{ background-color: rgba({r},{g},{b},{tint}); "
            f"border-left: 3px solid {color}; }}\n"
            f".deskcal button.block.{k}:hover, .deskcal button.chip.tinted.{k}:hover "
            f"{{ background-color: rgba({r},{g},{b},{tint + 0.15:.2f}); }}\n")
    return "".join(rules)
