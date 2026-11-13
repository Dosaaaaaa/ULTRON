"""One icon set, drawn once: 24x24, 1.75 px round strokes, no fills. Coloured per state at paint
time so a checked or hovered button never needs a second asset."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    # a handset
    "phone": (
        '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2'
        'A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>'
    ),
    # a notebook with a bookmark: what ULTRON remembers
    "memory": (
        '<path d="M6 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6z"/><path d="M6 3v18"/>'
        '<path d="M10 3v7l2-1.5 2 1.5V3"/>'
    ),
    # sliders: system and settings
    "system": (
        '<path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="2"/>'
        '<circle cx="9" cy="17" r="2"/>'
    ),
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
    "mic": (
        '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'
    ),
    "plus": '<path d="M12 5v14M5 12h14"/>',
}

STATES = {
    "normal": "#7fb7e6",
    "hover": "#d4efff",
    "checked": "#ffffff",
    "disabled": "#4a5b70",
}


def _svg(name: str, colour: str) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{colour}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'
        f"{_PATHS[name]}</svg>"
    ).encode()


def pixmap(name: str, colour: str, size: int = 18) -> QPixmap:
    ratio = 2  # drawn at twice the size so it stays sharp on high-density screens
    img = QImage(size * ratio, size * ratio, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    QSvgRenderer(QByteArray(_svg(name, colour))).render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    pix = QPixmap.fromImage(img)
    pix.setDevicePixelRatio(ratio)
    return pix


def icon(name: str, size: int = 18) -> QIcon:
    """Normal, hovered (Active), checked (Selected) and disabled faces of one icon."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, STATES["normal"], size), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, STATES["hover"], size), QIcon.Mode.Active)
    ic.addPixmap(pixmap(name, STATES["checked"], size), QIcon.Mode.Selected)
    ic.addPixmap(pixmap(name, STATES["disabled"], size), QIcon.Mode.Disabled)
    return ic


def names() -> list[str]:
    return sorted(_PATHS)
