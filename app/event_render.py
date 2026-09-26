"""How a calendar event block is drawn — shared by Day View and Week View.

Kept in its own module because both views must render events identically
(spec Parts 25 and 40): same colour resolution, same transparency handling,
same wrapping, same title/time spacing. A second copy of this logic in the
week view is exactly how the two would drift apart.

Three rules here are worth stating, because each replaces something that
looked fine at one font size and broke at another:

1.  **Layout comes from font metrics, never fixed pixels.** The title used to
    be drawn at y+2 and the time at a hard-coded y+18. That assumed one exact
    font size; at a larger application font the title simply grew down into
    the time and the two collided. Everything here measures the actual font.

2.  **Transparency applies to the BACKGROUND only.** Fading the whole block
    would fade the text with it and make the event unreadable, which the
    spec rules out explicitly. The fill gets the alpha; the text is drawn at
    full opacity, in whichever of black/white contrasts better with the
    resulting background.

3.  **A short event degrades in a defined order.** When there isn't room for
    both lines, the title wins — it is the thing that identifies the event,
    and the time is already implied by the block's position on the timeline.
    Below even one line, the text is elided rather than allowed to spill.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen

# Padding inside an event block, in pixels. Small and font-independent: this
# is breathing room against the border, not text layout.
PAD_X = 6
PAD_Y = 2

# How long a new event is when its length was not dragged out — the
# double-click-on-empty-space gesture. One value, used by both views.
DEFAULT_EVENT_MINUTES = 60


def hour_font(base: QFont) -> QFont:
    """The font the hour labels down the left edge are drawn in.

    One definition for both views. It existed twice — a method on the Day
    View's timeline and a module function in the week view — which is the
    shape of thing that eventually drifts and leaves the two grids
    disagreeing about where their own gridlines are.
    """
    font = QFont(base)
    font.setPointSizeF(max(6.0, font.pointSizeF() - 1.0))
    return font


def gutter_width(base: QFont) -> int:
    """Width of the hour-label column, measured rather than assumed.

    This was a fixed 62px, which was fine at the default application font and
    clipped to "00 AM" as soon as the font grew. Measuring the widest label
    the column will actually contain makes it correct at every font size, and
    costs one text measurement per repaint.
    """
    from .day_calendar_model import format_minute

    metrics = QFontMetrics(hour_font(base))
    widest = max(metrics.horizontalAdvance(format_minute(h * 60)) for h in (0, 10, 22))
    return widest + 14


def resolve_event_color(event, theme_accent: QColor) -> QColor:
    """An event's colour, resolved at PAINT time rather than stored.

    An event with no explicit colour follows the current theme, and keeps
    following it when the theme changes — which is why the theme's colour is
    never written onto the event as though the user had chosen it. An event
    the user did colour keeps that colour across theme changes.
    """
    custom = getattr(event, "color", None)
    if custom:
        candidate = QColor(custom)
        if candidate.isValid():
            return candidate
    return QColor(theme_accent)


def resolve_event_opacity(event) -> int:
    """The event's background opacity as a percentage, 10-100.

    Stored 0-100 for legibility in the database, with a sentinel meaning
    "follow the application default" — the same idea as a null colour above,
    for the same reason: an event that hasn't been given an explicit
    transparency should follow the default rather than freeze whatever the
    default happened to be when it was created.
    """
    from .database import OPACITY_FOLLOWS_DEFAULT
    from .theme import DEFAULT_EVENT_TRANSPARENCY

    raw = getattr(event, "opacity", OPACITY_FOLLOWS_DEFAULT)
    try:
        raw = int(raw)
    except (TypeError, ValueError):
        raw = OPACITY_FOLLOWS_DEFAULT
    if raw == OPACITY_FOLLOWS_DEFAULT or raw < 0:
        raw = 100 - DEFAULT_EVENT_TRANSPARENCY
    return max(10, min(100, raw))


def apply_opacity(color: QColor, event) -> QColor:
    """Applies the event's background transparency to a copy of the colour.

    Background only — never the text. See rule 2 in the module docstring.
    """
    faded = QColor(color)
    faded.setAlpha(round(255 * resolve_event_opacity(event) / 100))
    return faded


def readable_text_color(background: QColor, surface: QColor | None = None) -> QColor:
    """Black or white, whichever contrasts better with the background.

    Uses relative luminance rather than a naive average so that a saturated
    mid-tone (a strong green, say) still gets the right answer.

    A translucent fill shows the pane behind it, so the luminance that
    matters is the COMPOSITE of the two — which means the answer depends on
    the theme. `surface` is the colour actually behind the event; without it
    this assumed white, which is why a 30%-opaque event on a dark theme could
    be given black text and become unreadable. Now that events default to
    being mostly transparent, that assumption had to go.
    """
    surface = surface if surface is not None else QColor("#ffffff")
    alpha = background.alphaF()
    r = background.redF() * alpha + surface.redF() * (1 - alpha)
    g = background.greenF() * alpha + surface.greenF() * (1 - alpha)
    b = background.blueF() * alpha + surface.blueF() * (1 - alpha)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return QColor("#101010") if luminance > 0.55 else QColor("#ffffff")


def paint_event_block(painter: QPainter, rect: QRect, event, theme_accent: QColor,
                       selected: bool = False, palette=None,
                       base_font: QFont | None = None, show_time: bool = True,
                       surface: QColor | None = None):
    """Draws one event. Used unchanged by both Day View and Week View.

    `theme_accent` is the colour a theme-following event takes, and `surface`
    is what is behind the block — both come from the application's own
    ColorScheme, not from the widget palette (see theme.event_color).
    """
    base_color = resolve_event_color(event, theme_accent)
    fill = apply_opacity(base_color, event)
    if getattr(event, "done", False):
        fill.setAlpha(max(40, fill.alpha() // 2))

    painter.save()
    painter.setBrush(fill)
    painter.setPen(QPen(base_color.darker(150), 2 if selected else 1))
    painter.drawRoundedRect(rect, 4, 4)

    text_color = readable_text_color(fill, surface)
    painter.setPen(text_color)

    title_font = QFont(base_font) if base_font is not None else QFont(painter.font())
    title_font.setBold(True)
    if getattr(event, "done", False):
        title_font.setStrikeOut(True)
    time_font = QFont(base_font) if base_font is not None else QFont(painter.font())
    time_font.setPointSizeF(max(6.0, time_font.pointSizeF() * 0.9))

    title_metrics = QFontMetrics(title_font)
    time_metrics = QFontMetrics(time_font)

    inner = rect.adjusted(PAD_X, PAD_Y, -PAD_X, -PAD_Y)
    if inner.width() <= 0 or inner.height() <= 0:
        painter.restore()
        return

    title = event.title or "(untitled)"
    time_text = _format_span(event) if show_time else ""

    title_line = title_metrics.height()
    time_line = time_metrics.height()
    # A gap proportional to the text itself, so it stays visually even as the
    # application font changes instead of being right at one size only.
    gap = max(1, round(title_metrics.leading() + title_line * 0.12))

    painter.setFont(title_font)
    room_for_both = inner.height() >= title_line + gap + time_line

    if room_for_both:
        # Give the title every line that isn't needed by the time row.
        title_area = QRect(inner.left(), inner.top(), inner.width(),
                           inner.height() - time_line - gap)
        max_lines = max(1, title_area.height() // title_line)
        for index, line in enumerate(_wrap_lines(title, title_metrics,
                                                  title_area.width(), max_lines)):
            painter.drawText(
                QRect(title_area.left(), title_area.top() + index * title_line,
                      title_area.width(), title_line),
                Qt.AlignLeft | Qt.AlignTop, line,
            )
        painter.setFont(time_font)
        time_area = QRect(inner.left(), inner.bottom() - time_line + 1,
                          inner.width(), time_line)
        painter.drawText(time_area, Qt.AlignLeft | Qt.AlignVCenter,
                         time_metrics.elidedText(time_text, Qt.ElideRight, inner.width()))
    elif inner.height() >= title_line:
        # Only one line fits: the title identifies the event, and the time is
        # already implied by where the block sits on the timeline.
        painter.drawText(inner, Qt.AlignLeft | Qt.AlignVCenter,
                         title_metrics.elidedText(title, Qt.ElideRight, inner.width()))
    else:
        # Shorter than a single line — draw a clipped title rather than
        # letting anything spill outside the block.
        painter.setClipRect(inner)
        painter.drawText(inner, Qt.AlignLeft | Qt.AlignTop,
                         title_metrics.elidedText(title, Qt.ElideRight, inner.width()))

    painter.restore()


def _wrap_lines(text: str, metrics: QFontMetrics, width: int, max_lines: int) -> list:
    """Wraps text to `width`, eliding rather than clipping (Part 19).

    Qt's own TextWordWrap flag wraps correctly but has no notion of "and
    stop here" — a word wider than the block (a long title in a narrow week
    column, or any title at a large application font) is drawn cut off
    mid-letter, and text past the last visible line just disappears. Both
    read as a rendering bug rather than as truncation.

    So the wrapping is done here, with three rules:
      * lines break on words, greedily, exactly like ordinary wrapping;
      * a single word too wide for the block still takes a line, and is
        elided with "…" instead of being sliced;
      * whatever doesn't fit in the available lines is folded into the last
        one and elided there, so the ellipsis says "there is more" — the
        full text is always in the tooltip.

    Returns the lines ready to draw; the caller positions them using the
    same font metrics, so vertical spacing follows the font (Part 20).
    """
    if max_lines <= 0 or width <= 0 or not text:
        return []
    words = text.split()
    lines: list = []
    current = ""
    index = 0
    while index < len(words):
        word = words[index]
        candidate = f"{current} {word}" if current else word
        if not current or metrics.horizontalAdvance(candidate) <= width:
            current = candidate
            index += 1
        else:
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
        index = len(words)
    if index < len(words) and lines:
        lines[-1] = " ".join([lines[-1]] + words[index:])
    return [metrics.elidedText(line, Qt.ElideRight, width) for line in lines]


def event_tooltip(event) -> str:
    """Full text for hovering, so nothing is unreachable when elided."""
    parts = [event.title or "(untitled)"]
    if not getattr(event, "all_day", False):
        parts.append(_format_span(event))
    if getattr(event, "notes", ""):
        parts.append("")
        parts.append(event.notes)
    return "\n".join(parts)


def _format_span(event) -> str:
    """The time text for an event block or tooltip.

    For one day's piece of a longer event (recurrence.DayPiece) this is the
    whole event's span, not the piece's: the Tuesday part of a Monday 9 PM
    to Wednesday 2 AM event says "Mon 9:00 PM – Wed 2:00 AM", the same as
    its other parts, because it is the same event.
    """
    from .day_calendar_model import format_minute
    from .date_state import to_qdate
    if getattr(event, "all_day", False):
        return "Untimed"
    occurrence = getattr(event, "occurrence", None)
    if occurrence is None:
        return f"{format_minute(event.start_minute)} – {format_minute(event.end_minute)}"
    start, end = occurrence.start_minute, occurrence.end_minute
    if len(occurrence.days()) == 1:
        return f"{format_minute(start)} – {format_minute(end)}"
    first = to_qdate(occurrence.date).toString("ddd")
    last = to_qdate(occurrence.end_date).toString("ddd")
    return f"{first} {format_minute(start)} – {last} {format_minute(end % (24 * 60))}"
