"""
Design system for PDF Master Toolkit.

A single Palette dataclass drives a generated Qt stylesheet. Two palettes ship
(Light and Dark); both derive every hover/pressed/border shade from the brand
accent in branding.py, so rebranding is a one-value change.

The visual language deliberately mirrors modern commercial Windows software:
8px grid, 10px card radius, hairline 1px borders, soft elevation, a single
saturated accent used sparingly, and Segoe UI Variable as the type face.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app import branding


# ---------------------------------------------------------------------------
# colour maths
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    clamp = lambda v: max(0, min(255, int(round(v))))  # noqa: E731
    return "#{:02X}{:02X}{:02X}".format(clamp(r), clamp(g), clamp(b))


def lighten(hex_color: str, amount: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex(r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount)


def darken(hex_color: str, amount: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex(r * (1 - amount), g * (1 - amount), b * (1 - amount))


def mix(a: str, b: str, t: float) -> str:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


def rgba(hex_color: str, alpha: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def readable_on(hex_color: str) -> str:
    """Pick black or white text for a given background (WCAG relative luminance)."""
    r, g, b = (c / 255 for c in _hex_to_rgb(hex_color))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    lum = 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    return "#FFFFFF" if lum < 0.45 else "#101828"


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool

    # surfaces, back to front
    canvas: str          # window background behind everything
    surface: str         # cards, panels
    surface_alt: str     # subtle inset areas, table stripes
    surface_hover: str
    sidebar: str
    sidebar_hover: str
    titlebar: str

    # lines
    border: str
    border_strong: str
    divider: str

    # type
    text: str
    text_muted: str
    text_subtle: str
    text_inverse: str

    # semantics
    accent: str
    accent_alt: str
    success: str
    warning: str
    danger: str
    info: str

    # controls
    input_bg: str
    input_border: str
    scroll_handle: str
    shadow: str

    # ---- derived ----------------------------------------------------------
    @property
    def accent_hover(self) -> str:
        return lighten(self.accent, 0.12) if self.dark else darken(self.accent, 0.08)

    @property
    def accent_pressed(self) -> str:
        return darken(self.accent, 0.18)

    @property
    def accent_soft(self) -> str:
        return mix(self.surface, self.accent, 0.14 if self.dark else 0.10)

    @property
    def accent_softer(self) -> str:
        return mix(self.surface, self.accent, 0.07 if self.dark else 0.05)

    @property
    def on_accent(self) -> str:
        return readable_on(self.accent)

    def soft(self, color: str, strength: float = 0.12) -> str:
        return mix(self.surface, color, strength)


LIGHT = Palette(
    name="Light",
    dark=False,
    canvas="#F4F6FA",
    surface="#FFFFFF",
    surface_alt="#F7F9FC",
    surface_hover="#F0F3F9",
    sidebar="#FFFFFF",
    sidebar_hover="#F2F5FA",
    titlebar="#FFFFFF",
    border="#E4E8F0",
    border_strong="#D2D8E4",
    divider="#EDF0F6",
    text="#141B2D",
    text_muted="#5A6684",
    text_subtle="#8792AB",
    text_inverse="#FFFFFF",
    accent=branding.BRAND_ACCENT,
    accent_alt=branding.BRAND_ACCENT_ALT,
    success=branding.BRAND_SUCCESS,
    warning=branding.BRAND_WARNING,
    danger=branding.BRAND_DANGER,
    info=branding.BRAND_INFO,
    input_bg="#FFFFFF",
    input_border="#DCE2EC",
    scroll_handle="#C7CFDD",
    shadow="rgba(16, 24, 40, 0.08)",
)

DARK = Palette(
    name="Dark",
    dark=True,
    canvas="#0E1219",
    surface="#161B24",
    surface_alt="#1B212C",
    surface_hover="#212936",
    sidebar="#12171F",
    sidebar_hover="#1D2431",
    titlebar="#12171F",
    border="#262E3B",
    border_strong="#354052",
    divider="#1F2733",
    text="#E9EDF5",
    text_muted="#9AA6BC",
    text_subtle="#6C7891",
    text_inverse="#0E1219",
    accent=lighten(branding.BRAND_ACCENT, 0.10),
    accent_alt=lighten(branding.BRAND_ACCENT_ALT, 0.10),
    success=lighten(branding.BRAND_SUCCESS, 0.08),
    warning=lighten(branding.BRAND_WARNING, 0.08),
    danger=lighten(branding.BRAND_DANGER, 0.08),
    info=lighten(branding.BRAND_INFO, 0.08),
    input_bg="#1A202B",
    input_border="#2C3543",
    scroll_handle="#39445608",
    shadow="rgba(0, 0, 0, 0.45)",
)
DARK = replace(DARK, scroll_handle="#394456")


PALETTES = {"Light": LIGHT, "Dark": DARK}


# ---------------------------------------------------------------------------
# typography + metrics
# ---------------------------------------------------------------------------

FONT_STACK = '"Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans", Arial, sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New", monospace'

RADIUS_SM = 6
RADIUS = 10
RADIUS_LG = 14
SPACE = 8


# ---------------------------------------------------------------------------
# stylesheet
# ---------------------------------------------------------------------------

def build_stylesheet(p: Palette, ui_scale: float = 1.0) -> str:
    """Return the full application QSS for the given palette."""

    s = lambda v: int(round(v * ui_scale))  # noqa: E731

    base = s(14)
    small = s(12)
    tiny = s(11)

    return f"""
/* ===================== GLOBAL ===================== */
* {{
    outline: 0;
}}
QWidget {{
    font-family: {FONT_STACK};
    font-size: {base}px;
    color: {p.text};
}}
QMainWindow, QDialog {{
    background: {p.canvas};
}}
QToolTip {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 6px 9px;
    font-size: {small}px;
}}

/* ===================== SHELL ===================== */
#AppCanvas {{ background: {p.canvas}; }}

#Sidebar {{
    background: {p.sidebar};
    border-right: 1px solid {p.border};
}}
#SidebarHeader {{
    background: transparent;
    border-bottom: 1px solid {p.divider};
}}
#SidebarFooter {{
    background: transparent;
    border-top: 1px solid {p.divider};
}}
#BrandName {{
    font-size: {s(15)}px;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: {p.text};
}}
#BrandTagline {{
    font-size: {tiny}px;
    color: {p.text_subtle};
}}
#LogoMark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p.accent}, stop:1 {p.accent_alt});
    border-radius: {RADIUS}px;
    color: #FFFFFF;
    font-size: {s(15)}px;
    font-weight: 800;
}}

#NavSection {{
    color: {p.text_subtle};
    font-size: {tiny}px;
    font-weight: 700;
    letter-spacing: 0.9px;
    padding: 14px 16px 6px 16px;
    background: transparent;
}}

QPushButton#NavItem {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 0px 10px;
    margin: 1px 8px;
    min-height: {s(36)}px;
    text-align: left;
    color: {p.text_muted};
    font-size: {s(13)}px;
    font-weight: 500;
}}
QPushButton#NavItem:hover {{
    background: {p.sidebar_hover};
    color: {p.text};
}}
QPushButton#NavItem:checked {{
    background: {p.accent_soft};
    color: {p.accent};
    font-weight: 650;
}}

#SearchBox {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS_SM}px;
    padding: 7px 10px;
    font-size: {s(13)}px;
    color: {p.text};
}}
#SearchBox:focus {{
    border: 1px solid {p.accent};
    background: {p.surface};
}}

/* ===================== HEADER BAR ===================== */
#HeaderBar {{
    background: {p.titlebar};
    border-bottom: 1px solid {p.border};
}}
#PageTitle {{
    font-size: {s(19)}px;
    font-weight: 700;
    letter-spacing: -0.3px;
    color: {p.text};
}}
#PageSubtitle {{
    font-size: {small}px;
    color: {p.text_muted};
}}
QPushButton#HeaderIconButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    min-width: {s(32)}px;
    min-height: {s(32)}px;
    font-size: {s(15)}px;
    color: {p.text_muted};
}}
QPushButton#HeaderIconButton:hover {{
    background: {p.surface_hover};
    border-color: {p.border};
    color: {p.text};
}}

/* ===================== CARDS ===================== */
#Card, QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
}}
#CardFlat {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
}}
#CardTitle {{
    font-size: {s(15)}px;
    font-weight: 650;
    color: {p.text};
}}
#CardSubtitle {{
    font-size: {small}px;
    color: {p.text_muted};
}}
#SectionTitle {{
    font-size: {s(13)}px;
    font-weight: 700;
    letter-spacing: 0.4px;
    color: {p.text_subtle};
}}
#StatValue {{
    font-size: {s(28)}px;
    font-weight: 750;
    letter-spacing: -0.8px;
    color: {p.text};
}}
#StatLabel {{
    font-size: {small}px;
    font-weight: 600;
    color: {p.text_muted};
}}
#StatDelta {{
    font-size: {tiny}px;
    font-weight: 600;
    color: {p.success};
}}
#Muted {{ color: {p.text_muted}; }}
#Subtle {{ color: {p.text_subtle}; font-size: {small}px; }}
#Danger {{ color: {p.danger}; }}
#Success {{ color: {p.success}; }}
#Warning {{ color: {p.warning}; }}
#Mono {{ font-family: {MONO_STACK}; font-size: {small}px; }}

#Hero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p.accent}, stop:1 {p.accent_alt});
    border-radius: {RADIUS_LG}px;
    border: none;
}}
#HeroTitle {{ color: #FFFFFF; font-size: {s(22)}px; font-weight: 750; }}
#HeroText  {{ color: rgba(255,255,255,0.86); font-size: {s(13)}px; }}

/* ===================== BUTTONS ===================== */
QPushButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 8px 16px;
    font-size: {s(13)}px;
    font-weight: 600;
    min-height: {s(20)}px;
}}
QPushButton:hover  {{ background: {p.surface_hover}; border-color: {p.border_strong}; }}
QPushButton:pressed{{ background: {p.surface_alt}; }}
QPushButton:disabled {{
    color: {p.text_subtle};
    background: {p.surface_alt};
    border-color: {p.border};
}}

QPushButton#Primary {{
    background: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
    font-weight: 650;
}}
QPushButton#Primary:hover   {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_pressed}; }}
QPushButton#Primary:disabled {{
    background: {p.soft(p.accent, 0.25)};
    border-color: transparent;
    color: {rgba('#FFFFFF', 0.65)};
}}

QPushButton#PrimaryLarge {{
    background: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
    border-radius: {RADIUS}px;
    padding: 12px 26px;
    font-size: {s(14)}px;
    font-weight: 700;
    min-height: {s(26)}px;
}}
QPushButton#PrimaryLarge:hover   {{ background: {p.accent_hover}; }}
QPushButton#PrimaryLarge:pressed {{ background: {p.accent_pressed}; }}
QPushButton#PrimaryLarge:disabled {{
    background: {p.soft(p.accent, 0.25)}; border-color: transparent;
}}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {p.text_muted};
    font-weight: 600;
}}
QPushButton#Ghost:hover {{ background: {p.surface_hover}; color: {p.text}; }}

QPushButton#DangerButton {{
    background: {p.danger};
    color: #FFFFFF;
    border: 1px solid {p.danger};
}}
QPushButton#DangerButton:hover {{ background: {darken(p.danger, 0.10)}; }}

QPushButton#DangerGhost {{
    background: transparent;
    color: {p.danger};
    border: 1px solid {p.soft(p.danger, 0.35)};
}}
QPushButton#DangerGhost:hover {{ background: {p.soft(p.danger, 0.12)}; }}

QPushButton#QuickAction {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
    padding: 14px 12px;
    text-align: left;
    font-size: {s(13)}px;
    font-weight: 600;
    min-height: {s(46)}px;
}}
QPushButton#QuickAction:hover {{
    border-color: {p.accent};
    background: {p.accent_softer};
}}

QPushButton#ChipButton {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: 14px;
    padding: 5px 13px;
    font-size: {small}px;
    font-weight: 600;
    color: {p.text_muted};
    min-height: 0px;
}}
QPushButton#ChipButton:hover  {{ border-color: {p.accent}; color: {p.accent}; }}
QPushButton#ChipButton:checked {{
    background: {p.accent_soft}; border-color: {p.accent}; color: {p.accent};
}}

QPushButton#ToolbarButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    padding: 6px 9px;
    font-size: {small}px;
    color: {p.text_muted};
    min-height: {s(24)}px;
}}
QPushButton#ToolbarButton:hover   {{ background: {p.surface_hover}; color: {p.text}; }}
QPushButton#ToolbarButton:checked {{ background: {p.accent_soft}; color: {p.accent}; }}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    padding: 5px;
    color: {p.text_muted};
}}
QToolButton:hover {{ background: {p.surface_hover}; color: {p.text}; }}

/* ===================== INPUTS ===================== */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QDateEdit {{
    background: {p.input_bg};
    border: 1px solid {p.input_border};
    border-radius: {RADIUS_SM}px;
    padding: 7px 10px;
    selection-background-color: {p.accent};
    selection-color: #FFFFFF;
    color: {p.text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
    border: 1px solid {p.accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {p.surface_alt}; color: {p.text_subtle};
}}
QLineEdit[error="true"] {{ border: 1px solid {p.danger}; }}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
    subcontrol-origin: border;
}}
QSpinBox::up-button   {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {p.text_subtle};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p.text_subtle};
}}

QComboBox {{
    background: {p.input_bg};
    border: 1px solid {p.input_border};
    border-radius: {RADIUS_SM}px;
    padding: 7px 30px 7px 10px;
    color: {p.text};
    min-height: {s(18)}px;
}}
QComboBox:hover {{ border-color: {p.border_strong}; }}
QComboBox:focus, QComboBox:on {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p.text_muted};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.accent};
    outline: none;
}}

QCheckBox, QRadioButton {{ spacing: 8px; color: {p.text}; font-size: {s(13)}px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator {{
    border: 1px solid {p.border_strong};
    border-radius: 4px;
    background: {p.input_bg};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url(:/qt-project.org/styles/commonstyle/images/checkbox_checked.png);
}}
QCheckBox::indicator:disabled {{ background: {p.surface_alt}; border-color: {p.border}; }}
QRadioButton::indicator {{
    border: 1px solid {p.border_strong};
    border-radius: 9px;
    background: {p.input_bg};
}}
QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QRadioButton::indicator:checked {{
    border: 5px solid {p.accent};
    background: {p.surface};
}}

QSlider::groove:horizontal {{
    height: 4px; background: {p.border}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 15px; height: 15px; margin: -6px 0;
    background: {p.surface}; border: 2px solid {p.accent}; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {p.accent_soft}; }}

/* ===================== TABS ===================== */
QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
    background: {p.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 16px;
    margin-right: 2px;
    color: {p.text_muted};
    font-size: {s(13)}px;
    font-weight: 600;
}}
QTabBar::tab:hover    {{ color: {p.text}; }}
QTabBar::tab:selected {{ color: {p.accent}; border-bottom: 2px solid {p.accent}; }}

/* ===================== LISTS / TABLES / TREES ===================== */
QListWidget, QTreeWidget, QTreeView, QListView, QTableWidget, QTableView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
    alternate-background-color: {p.surface_alt};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
    padding: 4px;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 8px;
    border-radius: {RADIUS_SM}px;
    margin: 1px 2px;
}}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {p.surface_hover}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {p.accent_soft}; color: {p.text};
}}

QTableWidget, QTableView {{ gridline-color: {p.divider}; padding: 0px; }}
QTableWidget::item, QTableView::item {{ padding: 7px 8px; border: none; }}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {p.accent_soft}; color: {p.text};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {p.surface_alt};
    color: {p.text_muted};
    border: none;
    border-bottom: 1px solid {p.border};
    border-right: 1px solid {p.divider};
    padding: 9px 8px;
    font-size: {small}px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
QHeaderView::section:first {{ border-top-left-radius: {RADIUS}px; }}
QHeaderView::section:last  {{ border-top-right-radius: {RADIUS}px; border-right: none; }}
QTableCornerButton::section {{ background: {p.surface_alt}; border: none; }}

/* ===================== SCROLLBARS ===================== */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {p.scroll_handle}; border-radius: 5px; min-height: 34px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_subtle}; }}
QScrollBar:horizontal {{
    background: transparent; height: 11px; margin: 0 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.scroll_handle}; border-radius: 5px; min-width: 34px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.text_subtle}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ===================== PROGRESS ===================== */
QProgressBar {{
    background: {p.surface_alt};
    border: none;
    border-radius: 5px;
    height: 9px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent}, stop:1 {p.accent_alt});
}}
QProgressBar#TableProgress {{ height: 7px; }}
QProgressBar#ProgressSuccess::chunk {{ background: {p.success}; }}
QProgressBar#ProgressDanger::chunk  {{ background: {p.danger}; }}

/* ===================== DROP ZONE ===================== */
#DropZone {{
    background: {p.surface_alt};
    border: 2px dashed {p.border_strong};
    border-radius: {RADIUS_LG}px;
}}
#DropZone[hover="true"] {{
    background: {p.accent_softer};
    border: 2px dashed {p.accent};
}}
#DropZoneTitle {{ font-size: {s(16)}px; font-weight: 650; color: {p.text}; }}
#DropZoneHint  {{ font-size: {small}px; color: {p.text_subtle}; }}
#DropZoneIcon  {{ font-size: {s(34)}px; color: {p.text_subtle}; }}

/* ===================== BADGES ===================== */
#Badge {{
    background: {p.surface_alt};
    color: {p.text_muted};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 2px 9px;
    font-size: {tiny}px;
    font-weight: 700;
}}
#BadgeAccent  {{ background: {p.soft(p.accent, 0.14)};  color: {p.accent};  border: 1px solid {p.soft(p.accent, 0.30)}; border-radius: 10px; padding: 2px 9px; font-size: {tiny}px; font-weight: 700; }}
#BadgeSuccess {{ background: {p.soft(p.success, 0.16)}; color: {p.success}; border: 1px solid {p.soft(p.success, 0.32)}; border-radius: 10px; padding: 2px 9px; font-size: {tiny}px; font-weight: 700; }}
#BadgeWarning {{ background: {p.soft(p.warning, 0.18)}; color: {p.warning}; border: 1px solid {p.soft(p.warning, 0.34)}; border-radius: 10px; padding: 2px 9px; font-size: {tiny}px; font-weight: 700; }}
#BadgeDanger  {{ background: {p.soft(p.danger, 0.16)};  color: {p.danger};  border: 1px solid {p.soft(p.danger, 0.32)}; border-radius: 10px; padding: 2px 9px; font-size: {tiny}px; font-weight: 700; }}

/* ===================== BANNERS ===================== */
#InfoBanner {{
    background: {p.soft(p.info, 0.12)};
    border: 1px solid {p.soft(p.info, 0.28)};
    border-radius: {RADIUS_SM}px;
    padding: 10px 12px;
    color: {p.text};
    font-size: {small}px;
}}
#WarnBanner {{
    background: {p.soft(p.warning, 0.14)};
    border: 1px solid {p.soft(p.warning, 0.30)};
    border-radius: {RADIUS_SM}px;
    padding: 10px 12px;
    color: {p.text};
    font-size: {small}px;
}}
#DangerBanner {{
    background: {p.soft(p.danger, 0.13)};
    border: 1px solid {p.soft(p.danger, 0.30)};
    border-radius: {RADIUS_SM}px;
    padding: 10px 12px;
    color: {p.text};
    font-size: {small}px;
}}
#SuccessBanner {{
    background: {p.soft(p.success, 0.13)};
    border: 1px solid {p.soft(p.success, 0.30)};
    border-radius: {RADIUS_SM}px;
    padding: 10px 12px;
    color: {p.text};
    font-size: {small}px;
}}

/* ===================== VIEWER ===================== */
#ViewerCanvas {{
    background: {darken(p.canvas, 0.10) if not p.dark else '#080B10'};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
}}
#ViewerToolbar {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
}}
#PageCanvas {{ background: transparent; }}
#ThumbStrip {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
}}

/* ===================== MISC ===================== */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical   {{ height: 6px; }}

QMenu {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: 5px;
    color: {p.text};
    font-size: {s(13)}px;
}}
QMenu::item:selected {{ background: {p.accent_soft}; color: {p.accent}; }}
QMenu::separator {{ height: 1px; background: {p.divider}; margin: 5px 8px; }}

QGroupBox {{
    border: 1px solid {p.border};
    border-radius: {RADIUS}px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 650;
    background: {p.surface};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {p.text_muted};
    font-size: {small}px;
    font-weight: 700;
}}

QStatusBar {{
    background: {p.surface};
    border-top: 1px solid {p.border};
    color: {p.text_muted};
    font-size: {small}px;
}}
QStatusBar::item {{ border: none; }}

#Separator {{ background: {p.divider}; max-height: 1px; min-height: 1px; border: none; }}
#VSeparator {{ background: {p.divider}; max-width: 1px; min-width: 1px; border: none; }}
"""


def qt_palette(p: Palette):
    """QPalette so native-drawn bits (tooltips, text cursors) match the theme."""
    from PySide6.QtGui import QColor, QPalette

    qp = QPalette()
    qp.setColor(QPalette.Window, QColor(p.canvas))
    qp.setColor(QPalette.WindowText, QColor(p.text))
    qp.setColor(QPalette.Base, QColor(p.input_bg))
    qp.setColor(QPalette.AlternateBase, QColor(p.surface_alt))
    qp.setColor(QPalette.Text, QColor(p.text))
    qp.setColor(QPalette.Button, QColor(p.surface))
    qp.setColor(QPalette.ButtonText, QColor(p.text))
    qp.setColor(QPalette.Highlight, QColor(p.accent))
    qp.setColor(QPalette.HighlightedText, QColor(p.on_accent))
    qp.setColor(QPalette.ToolTipBase, QColor(p.surface))
    qp.setColor(QPalette.ToolTipText, QColor(p.text))
    qp.setColor(QPalette.PlaceholderText, QColor(p.text_subtle))
    qp.setColor(QPalette.Link, QColor(p.accent))
    qp.setColor(QPalette.Disabled, QPalette.Text, QColor(p.text_subtle))
    qp.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(p.text_subtle))
    qp.setColor(QPalette.Disabled, QPalette.WindowText, QColor(p.text_subtle))
    return qp


def resolve(name: str) -> Palette:
    return PALETTES.get(name, LIGHT)
