"""Window color schemes and the default set of day tags/colors.

A ColorScheme is just five colors. A handful of presets are offered, and
any of them can be tweaked color-by-color into a saved "Custom" scheme —
see settings_dialog.py for the picker UI. The whole app stylesheet is
generated from whichever scheme is active, so there's exactly one place
(`build_stylesheet`) that needs to know about Qt style sheet syntax.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Optional

# Built-in tags offered in the day-marker picker (calendar highlighting).
# Unrelated to the window color scheme below.
DEFAULT_TAGS = [
    ("holiday", "Holiday", "#e0b400"),
    ("trip", "Trip / Travel", "#3f8ede"),
    ("milestone", "Milestone", "#9b59b6"),
    ("important", "Important", "#e74c3c"),
]

TAG_LOOKUP = {key: (label, color) for key, label, color in DEFAULT_TAGS}


@dataclass
class ColorScheme:
    name: str
    background: str  # main window background
    panel: str       # editors, lists, inputs, menus
    text: str        # primary text color
    accent: str      # buttons, selection, links, today's date outline
    border: str      # panel borders, splitters, table grid lines


PRESETS: dict[str, ColorScheme] = {
    "Light": ColorScheme("Light", "#fafaf7", "#ffffff", "#222222", "#3f8ede", "#d8d5cc"),
    "Dark": ColorScheme("Dark", "#1e1f22", "#26272b", "#e6e6e6", "#5aa0f0", "#3a3b40"),
    "Sepia": ColorScheme("Sepia", "#f4ecd8", "#fbf4e2", "#3b2f22", "#a9702b", "#ddcca6"),
    "Slate": ColorScheme("Slate", "#232830", "#2b313b", "#dce3ea", "#5ec2c0", "#3a4250"),
    "Forest": ColorScheme("Forest", "#eef2ea", "#f8faf6", "#243422", "#4c7a3f", "#cdd9c6"),
    "Ocean": ColorScheme("Ocean", "#eaf3f7", "#ffffff", "#1c313a", "#1f8fa8", "#c7dde5"),
    "Rose Quartz": ColorScheme("Rose Quartz", "#faf0f2", "#fffbfc", "#3a2429", "#c96b83", "#eccdd4"),
    "Charcoal": ColorScheme("Charcoal", "#171717", "#212121", "#ececec", "#e8a33d", "#3a3a3a"),
    "Solarized Light": ColorScheme("Solarized Light", "#fdf6e3", "#ffffff", "#586e75", "#268bd2", "#eee8d5"),
    "Solarized Dark": ColorScheme("Solarized Dark", "#002b36", "#073642", "#93a1a1", "#2aa198", "#0a4552"),
    "Midnight": ColorScheme("Midnight", "#0f1226", "#171b34", "#d8dcf0", "#7c8cff", "#2a2f52"),
    "High Contrast": ColorScheme("High Contrast", "#ffffff", "#ffffff", "#000000", "#0047ab", "#000000"),
}

DEFAULT_PRESET_NAME = "Light"

# The default transparency of a calendar event's BACKGROUND, as a percentage
# (30 = 30% transparent = 70% opaque, Master Spec §26). Events the user
# hasn't given an explicit transparency follow this; see
# database.OPACITY_FOLLOWS_DEFAULT. It was 70 (30% opaque) before Group 3;
# because such events store the sentinel, changing this constant changed
# them all without rewriting a row.
DEFAULT_EVENT_TRANSPARENCY = 30


def event_color(scheme: ColorScheme) -> str:
    """The colour a theme-following calendar event is drawn in.

    Its own function rather than an inline `scheme.accent` at the two call
    sites, so "which part of the theme are events coloured from" has one
    answer that can change once.

    This exists because of a real bug: both calendars used to resolve a
    theme-following event's colour from `widget.palette().highlight()`. A Qt
    STYLESHEET — which is how this app themes itself — does not alter a
    widget's QPalette, so that highlight colour was Qt's own default blue in
    every single theme, and never changed when the theme did. The events were
    following something; it just wasn't the theme. Reading the ColorScheme
    directly is the fix, and it is why the views are handed the scheme.
    """
    return scheme.accent


def scheme_to_json(scheme: ColorScheme) -> str:
    return json.dumps(asdict(scheme))


def scheme_from_json(value: Optional[str]) -> ColorScheme:
    if not value:
        return replace(PRESETS[DEFAULT_PRESET_NAME])
    try:
        data = json.loads(value)
        return ColorScheme(
            name=data.get("name", "Custom"),
            background=data["background"],
            panel=data["panel"],
            text=data["text"],
            accent=data["accent"],
            border=data["border"],
        )
    except (ValueError, KeyError, TypeError):
        return replace(PRESETS[DEFAULT_PRESET_NAME])


def is_dark(scheme: ColorScheme) -> bool:
    r, g, b = _hex_to_rgb(scheme.background)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return luminance < 128


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(hex_a: str, hex_b: str, t: float) -> str:
    """Linearly blend two hex colors; t=0 -> hex_a, t=1 -> hex_b."""
    ar, ag, ab = _hex_to_rgb(hex_a)
    br, bg, bb = _hex_to_rgb(hex_b)
    return _rgb_to_hex((
        ar + (br - ar) * t,
        ag + (bg - ag) * t,
        ab + (bb - ab) * t,
    ))


# How much larger a section heading (the date above a journal entry, pane
# titles) is than ordinary interface text. A RATIO, not a pixel size — the
# whole point is that it tracks the application font-size setting instead of
# ignoring it.
HEADING_SCALE = 1.15
# Subtle explanatory text (pane hints, status lines, metadata). Also a ratio:
# several widgets used to hard-code "font-size: 11px" in their own
# stylesheets, which ignored the application font-size setting for the same
# reason the heading did.
HINT_SCALE = 0.85


def build_stylesheet(scheme: ColorScheme, ui_point_size: int = 10) -> str:
    """Builds the application stylesheet.

    `ui_point_size` is the user's application font-size setting, and it is a
    required part of the theme rather than an afterthought: a Qt stylesheet
    `font-size` OVERRIDES any font set on the widget or on QApplication, so
    a hard-coded size here silently defeats the setting entirely. That is
    exactly what used to happen — `QLabel#DateHeader` carried a literal
    `font-size: 15px`, so the date above a journal entry could never respond
    to the application font size no matter what it was set to.

    The rule for this file: never state an absolute font size. Anything that
    needs to differ from ordinary interface text derives from
    `ui_point_size` so that it scales with it.
    """
    heading_pt = max(1, round(ui_point_size * HEADING_SCALE))
    hint_pt = max(1, round(ui_point_size * HINT_SCALE))
    hover = mix(scheme.panel, scheme.accent, 0.18)
    pressed = mix(scheme.panel, scheme.accent, 0.32)
    tab_selected = mix(scheme.panel, scheme.accent, 0.22)
    return f"""
QWidget {{ background-color: {scheme.background}; color: {scheme.text}; }}
QMainWindow, QDialog {{ background-color: {scheme.background}; }}
QTextEdit, QPlainTextEdit, QTextBrowser, QLineEdit, QListWidget {{
    background-color: {scheme.panel}; color: {scheme.text};
    border: 1px solid {scheme.border}; border-radius: 4px;
}}
QCalendarWidget QWidget {{ background-color: {scheme.panel}; color: {scheme.text}; }}
QPushButton {{
    background-color: {scheme.panel}; border: 1px solid {scheme.border};
    border-radius: 4px; padding: 5px 10px; color: {scheme.text};
}}
QPushButton:hover {{ background-color: {hover}; }}
QPushButton:pressed {{ background-color: {pressed}; }}
QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 3px; color: {scheme.text}; }}
QToolButton:hover {{ background-color: {hover}; }}
QToolButton:checked {{ background-color: {tab_selected}; }}
QSplitter::handle {{ background-color: {scheme.border}; }}
QMenuBar {{ background-color: {scheme.panel}; color: {scheme.text}; }}
QMenu {{ background-color: {scheme.panel}; color: {scheme.text}; border: 1px solid {scheme.border}; }}
QMenu::item:selected {{ background-color: {hover}; }}
QStatusBar {{ background-color: {scheme.panel}; color: {scheme.text}; }}
QLabel#DateHeader {{ font-weight: 600; font-size: {heading_pt}pt; color: {scheme.text}; }}
QLabel#SectionHeading {{ font-weight: 600; color: {scheme.text}; }}
QLabel#SubtleHint {{ font-size: {hint_pt}pt; color: {mix(scheme.text, scheme.panel, 0.45)}; }}
QTabWidget::pane {{ border: 1px solid {scheme.border}; }}
QTabBar::tab {{
    background-color: {scheme.panel}; color: {scheme.text}; padding: 6px 14px;
    border: 1px solid {scheme.border}; border-bottom: none;
}}
QTabBar::tab:selected {{ background-color: {tab_selected}; }}
QTabBar::tab:hover {{ background-color: {hover}; }}
"""
