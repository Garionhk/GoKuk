"""One dark theme for the whole app.

Follows the EasyAI redesign: near-black surfaces, a single warm orange accent,
small uppercase letter-spaced section labels, and a monospace face reserved for
numbers the user might read back - pixel sizes, megapixels, the engine address.
Keeping the numeric readouts monospaced is the point of that split: they are
data, and they should not shift about as the values change.

Everything lives in one stylesheet string so the look has exactly one home.
"""
from __future__ import annotations

from pathlib import Path

from app.paths import resource_dir

# --- palette --------------------------------------------------------------
ACCENT = "#ff6b1a"
ACCENT_2 = "#ffa04a"
ACCENT_INK = "#120800"          # text that sits on the accent

BG = "#070709"
SURFACE = "#0e0e11"
SURFACE_2 = "#15151a"
SURFACE_3 = "#1c1c22"
INPUT_BG = "#0b0b0e"

LINE = "#26262d"
LINE_2 = "#33333c"

TEXT = "#f3f3f5"
MUTED = "#8b8b96"
DIM = "#5f5f6a"
FAINT = "#4a4a55"

OK = "#4ac97e"
WARN = "#ffa04a"
BAD = "#ff5f56"

# Kept for older references.
BG_PANEL = SURFACE_2
BG_INPUT = INPUT_BG
BORDER = LINE
TEXT_DIM = MUTED
ACCENT_HOVER = ACCENT_2
ACCENT_DOWN = "#e05a12"

#: Where to drop Manrope / JetBrains Mono if you want the exact designed type.
FONT_DIR = resource_dir() / "app" / "ui" / "fonts"

#: Filled in by load_fonts(); the stylesheet is built from these.
UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"


def load_fonts() -> tuple[str, str]:
    """Pick the best available text and numeric faces.

    The design asks for Manrope and JetBrains Mono. Neither ships with Windows,
    so any .ttf/.otf sitting in app/ui/fonts is registered first and used if it
    turns out to be one of them; otherwise we fall back to faces every Windows
    machine already has. A viewer should never see a broken layout because a
    font is missing.
    """
    global UI_FONT, MONO_FONT
    from PySide6.QtGui import QFontDatabase

    if FONT_DIR.is_dir():
        for path in sorted(FONT_DIR.glob("*.[to]tf")):
            QFontDatabase.addApplicationFont(str(path))

    families = set(QFontDatabase.families())

    def first(*names: str) -> str:
        for name in names:
            if name in families:
                return name
        return names[-1]

    UI_FONT = first("Manrope", "Inter", "Segoe UI Variable Text", "Segoe UI", "Arial")
    MONO_FONT = first("JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New")
    return UI_FONT, MONO_FONT


def stylesheet() -> str:
    """The whole application stylesheet, built for the resolved fonts."""
    return f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "{UI_FONT}";
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* --- tabs --------------------------------------------------------- */
QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {DIM};
    padding: 11px 22px 12px 22px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 14px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

/* --- panels and labels -------------------------------------------- */
QFrame#Panel {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 12px;
}}
QLabel#Heading {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    color: {MUTED};
}}
QLabel#Title {{ font-size: 15px; font-weight: 700; color: {TEXT}; }}
QLabel#Hint {{ color: {DIM}; font-size: 12px; }}
QLabel#Mono {{
    font-family: "{MONO_FONT}";
    font-size: 11px;
    color: {MUTED};
}}
QLabel#MonoAccent {{
    font-family: "{MONO_FONT}";
    font-size: 12px;
    color: {ACCENT};
    font-weight: 600;
}}
QLabel#Counter {{
    font-family: "{MONO_FONT}";
    font-size: 11px;
    color: {FAINT};
}}
QLabel#Warning {{ color: {WARN}; font-size: 12px; }}
QLabel#Good {{ color: {OK}; font-size: 12px; }}
QLabel#Big {{ font-size: 17px; font-weight: 700; }}

/* A small rounded count, sitting beside a section label. */
QLabel#Badge {{
    font-family: "{MONO_FONT}";
    font-size: 10px;
    font-weight: 700;
    color: {MUTED};
    background: {SURFACE_2};
    border: 1px solid {LINE};
    border-radius: 7px;
    padding: 1px 7px;
}}

/* The engine indicator under the menu bar. */
QLabel#EnginePill {{
    font-family: "{MONO_FONT}";
    font-size: 11px;
    color: {MUTED};
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 11px;
    padding: 3px 11px;
}}

/* --- text entry ---------------------------------------------------- */
QTextEdit, QLineEdit, QPlainTextEdit {{
    background: {INPUT_BG};
    border: 1px solid {LINE};
    border-radius: 12px;
    padding: 12px 14px;
    font-size: 14px;
    line-height: 150%;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QTextEdit:focus, QLineEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {LINE_2};
}}
QTextEdit[readOnly="true"] {{ background: {SURFACE}; }}

QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2};
    border: 1px solid {LINE};
    border-radius: 9px;
    padding: 7px 10px;
    min-height: 20px;
    color: {TEXT};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {LINE_2}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    border: 1px solid {LINE};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
    padding: 4px;
    outline: none;
}}

/* --- buttons ------------------------------------------------------- */
QPushButton {{
    height: 34px;
    padding: 0 14px;
    border-radius: 9px;
    border: 1px solid {LINE};
    background: {SURFACE_2};
    color: {MUTED};
    font-size: 12.5px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_3}; color: {TEXT}; border-color: {LINE_2}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: #3f3f49; border-color: #1e1e24; background: {SURFACE}; }}

QPushButton#Primary {{
    background: {ACCENT};
    border: none;
    color: {ACCENT_INK};
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 0.3px;
    height: 52px;
    border-radius: 12px;
}}
QPushButton#Primary:hover {{ background: {ACCENT_2}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton#Primary:disabled {{ background: {SURFACE_3}; color: #55555f; }}

QPushButton#Danger {{ border-color: {BAD}; color: {BAD}; }}
QPushButton#Danger:hover {{ background: #2a1614; color: {BAD}; }}

/* A chip in a segmented row: shape, and anything else pick-one. */
QPushButton#Chip {{
    height: 30px;
    padding: 0 12px;
    border-radius: 8px;
    border: 1px solid {LINE};
    background: {SURFACE_2};
    color: {MUTED};
    font-family: "{MONO_FONT}";
    font-size: 11.5px;
    font-weight: 600;
}}
QPushButton#Chip:hover {{ border-color: {LINE_2}; color: {TEXT}; }}
QPushButton#Chip:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: {ACCENT_INK};
}}
QPushButton#Chip:disabled {{ color: #3f3f49; border-color: #1e1e24; }}

/* --- workflow list ------------------------------------------------- */
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 10px;
    margin-bottom: 6px;
    padding: 2px;
}}
QListWidget::item:hover {{ border-color: {LINE_2}; }}
QListWidget::item:selected {{
    border: 1px solid {ACCENT};
    background: #1a1013;
}}

/* --- slider -------------------------------------------------------- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {SURFACE_3};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
    background: {ACCENT};
    border: 2px solid {BG};
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_2}; }}
QSlider:disabled::sub-page:horizontal {{ background: {LINE_2}; }}
QSlider:disabled::handle:horizontal {{ background: {LINE_2}; }}

/* --- progress ------------------------------------------------------ */
QProgressBar {{
    background: {SURFACE_3};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

/* --- scrollbars ---------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #2a2a32; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3b3b46; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #2a2a32; border-radius: 5px; min-width: 30px; }}

/* --- menus and status ---------------------------------------------- */
QMenuBar {{ background: {BG}; color: {MUTED}; border-bottom: 1px solid {LINE}; }}
QMenuBar::item {{ padding: 6px 12px; background: transparent; }}
QMenuBar::item:selected {{ color: {TEXT}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {LINE}; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}

QStatusBar {{ background: {BG}; color: {DIM}; border-top: 1px solid {LINE}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ font-size: 11px; color: {DIM}; }}

QToolTip {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {LINE};
    padding: 7px 9px;
    border-radius: 8px;
}}

/* --- misc ---------------------------------------------------------- */
QGroupBox {{
    border: 1px solid {LINE};
    border-radius: 10px;
    margin-top: 14px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {MUTED};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}
QCheckBox {{ color: {MUTED}; font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {LINE_2};
    border-radius: 5px;
    background: {SURFACE_2};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSplitter::handle {{ background: transparent; width: 10px; }}
QScrollArea {{ background: transparent; border: none; }}

/* Text and switches take the colour of whatever surface they sit on, so a
   label inside a panel does not show a darker box behind it. */
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QWidget#Transparent {{ background: transparent; }}
"""


#: Built once at startup by app.ui.theme.apply().
STYLESHEET = ""


#: Built by tools/make_icons.py. Ships with the program.
ICON_DIR = resource_dir() / "assets" / "icons"


def app_icon(name: str = "Gokuk"):
    """The window and taskbar icon, at every size Windows asks for.

    The .ico carries nine resolutions, so Windows picks the right one instead
    of scaling a single large image down and blurring it.
    """
    from PySide6.QtGui import QIcon

    icon = QIcon()
    ico = ICON_DIR / f"{name}.ico"
    if ico.is_file():
        icon.addFile(str(ico))
    for png in sorted(ICON_DIR.glob(f"{name}-*.png")):
        icon.addFile(str(png))
    return icon


def apply(app, icon_name: str = "Gokuk") -> None:
    """Load the fonts and icon, build the stylesheet, put it on the app."""
    global STYLESHEET
    load_fonts()
    STYLESHEET = stylesheet()
    app.setStyleSheet(STYLESHEET)

    icon = app_icon(icon_name)
    if not icon.isNull():
        app.setWindowIcon(icon)
