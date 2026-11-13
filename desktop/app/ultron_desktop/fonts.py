"""The interface typefaces: IBM Plex Sans for everything, IBM Plex Mono only for data (codes,
fingerprints, addresses). Bundled (SIL Open Font License) so the window looks the same everywhere;
if loading fails the system sans is used and nothing breaks."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

FONT_DIR = Path(__file__).parent / "fonts"
SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"


def load() -> bool:
    """Register the bundled faces; True if the sans is available afterwards."""
    ok = False
    for f in sorted(FONT_DIR.glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(f)) >= 0:
            ok = ok or f.name.startswith("IBMPlexSans")
    return ok


def apply_default(app) -> None:
    app.setFont(QFont(SANS if SANS in QFontDatabase.families() else app.font().family(), 10))
