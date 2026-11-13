"""The window's look.

World: the blue holographic orb the operator chose (kept). What the design system adds around it:
one typeface (IBM Plex Sans; Plex Mono only for data), a short type scale, one accent used for
actions and selection only, and every control showing hover, pressed, keyboard focus, disabled.
Red is reserved for the live microphone and for destructive actions, so it always means something.

Type scale (px): 12 small and secondary, 13 body, 15 panel and card titles, 20 the offline card.
Radius: 10 panels and cards, 8 buttons and fields. Spacing runs on 4 px steps.
"""

BG, PANEL, EDGE = "#0a1220", "#0c1626", "#1b2c44"
TEXT, DIM = "#e6f1fb", "#8fa6bd"
ACCENT, ACCENT_HI = "#4fb8ff", "#9bd8ff"
OK, WARN, DANGER = "#4cc38a", "#e0b341", "#e5534b"
LINE = "rgba(79, 184, 255, 70)"  # hairlines and field borders
LINE_HI = "rgba(124, 201, 255, 190)"  # hover
SURFACE = "rgba(7, 16, 30, 242)"  # floating panels over the orb
FIELD = "rgba(3, 12, 26, 210)"

QSS = f"""
* {{ font-family: "IBM Plex Sans", "Noto Sans", sans-serif; font-size: 13px; }}
QMainWindow, QDialog, QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: #11233b; color: {TEXT}; border: 1px solid {LINE}; padding: 4px 8px; }}
QLabel {{ background: transparent; }}
QLabel#dim {{ color: {DIM}; }}

/* ---- floating panels (Phone, Memory, System) ---- */
QFrame#panel {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#panel QLabel, QFrame#panel QCheckBox {{ background: transparent; }}
QLabel#panelTitle {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
QLabel#section {{ font-size: 12px; font-weight: 600; color: {ACCENT}; padding-top: 8px; }}
QLabel#data, QLabel#code {{ font-family: "IBM Plex Mono", monospace; color: {ACCENT_HI}; }}
QLabel#code {{ font-size: 22px; font-weight: 500; }}
QLabel#note {{ color: {DIM}; font-size: 12px; }}

/* ---- fields, lists ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser, QListWidget {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 8px;
    color: {TEXT}; selection-background-color: rgba(44, 111, 176, 200); }}
QLineEdit:hover, QComboBox:hover, QListWidget:hover {{ border-color: {LINE_HI}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QListWidget:focus {{ border: 2px solid {ACCENT_HI}; padding: 5px 7px; }}
QLineEdit:disabled {{ color: #4a5b70; }}
QPlainTextEdit#mono, QTextBrowser#mono {{ font-family: "IBM Plex Mono", monospace; }}
QListWidget {{ padding: 4px; }}
QListWidget::item {{ padding: 8px 8px; border-radius: 6px; }}
QListWidget::item:hover {{ background: rgba(28, 84, 150, 90); }}
QListWidget::item:selected {{ background: rgba(28, 84, 150, 210); color: #ffffff; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #0c1a2e; border: 1px solid {LINE}; selection-background-color: rgba(28, 84, 150, 210); }}

/* ---- buttons: one vocabulary ---- */
QPushButton {{ background: rgba(10, 28, 52, 170); border: 1px solid {LINE}; color: {TEXT}; border-radius: 8px;
    padding: 7px 14px; font-weight: 500; min-height: 18px; }}
QPushButton:hover {{ border-color: {LINE_HI}; background: rgba(24, 66, 120, 190); }}
QPushButton:pressed {{ background: rgba(32, 90, 160, 220); }}
QPushButton:focus {{ border: 2px solid {ACCENT_HI}; padding: 6px 13px; }}
QPushButton:disabled {{ color: #4a5b70; border-color: rgba(79, 184, 255, 30); background: rgba(10, 28, 52, 90); }}
QPushButton:checked {{ background: rgba(32, 90, 160, 220); border-color: {ACCENT_HI}; color: #ffffff; }}
QPushButton#primary {{ background: {ACCENT}; color: #04111f; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; border-color: {ACCENT_HI}; }}
QPushButton#primary:pressed {{ background: #6cc4ff; }}
QPushButton#primary:focus {{ border: 2px solid #ffffff; padding: 6px 13px; }}
QPushButton#primary:disabled {{ background: rgba(79, 184, 255, 70); color: rgba(4, 17, 31, 140); border-color: transparent; }}
QPushButton#danger {{ background: transparent; color: #ff8a84; border: 1px solid rgba(229, 83, 75, 140); }}
QPushButton#danger:hover {{ background: rgba(229, 83, 75, 50); border-color: {DANGER}; }}
QPushButton#danger:focus {{ border: 2px solid {DANGER}; padding: 6px 13px; }}
QPushButton#iconBtn {{ background: transparent; border: 1px solid transparent; padding: 0; border-radius: 15px; }}
QPushButton#iconBtn:hover {{ background: rgba(28, 84, 150, 140); }}
QPushButton#iconBtn:focus {{ border: 2px solid {ACCENT_HI}; }}
/* toolbar toggles: icon + label */
QPushButton#toolbtn {{ background: rgba(4, 12, 26, 150); border: 1px solid {LINE}; border-radius: 17px; padding: 0 14px 0 10px;
    min-height: 32px; max-height: 32px; color: #b9d6ee; }}
QPushButton#toolbtn:hover {{ border-color: {LINE_HI}; color: #ffffff; background: rgba(24, 66, 120, 170); }}
QPushButton#toolbtn:checked {{ background: rgba(32, 90, 160, 230); border-color: {ACCENT_HI}; color: #ffffff; }}
QPushButton#toolbtn:focus {{ border: 2px solid {ACCENT_HI}; padding: 0 13px 0 9px; }}
/* the controls under the orb */
QPushButton#hud {{ min-height: 30px; padding: 7px 18px; font-size: 14px; background: rgba(4, 12, 26, 170); }}
QPushButton#hud:hover {{ background: rgba(24, 66, 120, 190); }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid rgba(124, 201, 255, 140); border-radius: 4px; background: {FIELD}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT_HI}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_HI}; }}
QCheckBox:focus {{ color: #ffffff; }}
QCheckBox:disabled {{ color: #4a5b70; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(79, 184, 255, 90); border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(124, 201, 255, 150); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

/* ---- the offline card ---- */
QWidget#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; }}
QWidget#card QLabel {{ background: transparent; }}
QLabel#cardTitle {{ font-size: 20px; font-weight: 600; }}

/* ---- over the orb ---- */
QWidget#overlay, QLabel#wordmark, QLabel#hint, QLabel#ghostdim {{ background: transparent; }}
QLabel#wordmark {{ font-family: "IBM Plex Mono", monospace; font-weight: 500; color: {ACCENT}; font-size: 14px; letter-spacing: 6px; }}
QFrame#rule {{ background: rgba(79, 184, 255, 110); border: none; }}
QLabel#hint {{ color: #d4efff; font-size: 16px; font-weight: 500; }}
QLabel#ghostdim {{ color: {DIM}; font-size: 13px; }}
QWidget#captions {{ background: rgba(3, 10, 22, 214); border: 1px solid {LINE}; border-radius: 12px; }}
QLabel#capYou {{ color: #8fc3ea; font-size: 14px; }}
QLabel#capUltron {{ color: #f0f8ff; font-size: 17px; font-weight: 500; }}
QLabel#dots {{ font-size: 12px; }}
QLabel#key {{ color: {ACCENT}; font-size: 11px; font-weight: 600; border: 1px solid rgba(79, 184, 255, 120); border-radius: 4px; padding: 0 5px; min-width: 10px; }}
QLabel#keytext {{ color: rgba(143, 166, 189, 230); font-size: 12px; }}

/* ---- the microphone is always honest ---- */
QLabel#micLive {{ background: {DANGER}; color: white; font-weight: 600; padding: 5px 12px; border-radius: 12px; }}
QLabel#micOpen {{ color: {WARN}; padding: 5px 12px; border: 1px solid {WARN}; border-radius: 12px; }}
QLabel#micOff {{ color: {DIM}; padding: 5px 12px; border: 1px solid {EDGE}; border-radius: 12px; }}
"""
