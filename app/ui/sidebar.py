"""Collapsible navigation sidebar with live search."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from app import branding, config, paths
from app.ui.pages import BY_KEY, MODULES, ModuleSpec, by_section, search

EXPANDED_WIDTH = 244
COLLAPSED_WIDTH = 62


class Sidebar(QFrame):
    navigate = Signal(str)
    collapsed_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(EXPANDED_WIDTH)

        self._collapsed = False
        self._buttons: dict[str, QPushButton] = {}
        self._section_labels: list[QLabel] = []
        self._current = ""
        self._favourites: list[str] = list(config.get_list("favourite_tools", []))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_search())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_host = QWidget()
        self.nav_layout = QVBoxLayout(self.nav_host)
        self.nav_layout.setContentsMargins(0, 4, 0, 10)
        self.nav_layout.setSpacing(0)
        self.scroll.setWidget(self.nav_host)
        root.addWidget(self.scroll, 1)

        root.addWidget(self._build_footer())

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._build_nav()

    # -- header ------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("SidebarHeader")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(14, 14, 12, 14)
        lay.setSpacing(10)

        self.logo = QLabel()
        self.logo.setObjectName("LogoMark")
        self.logo.setFixedSize(36, 36)
        self.logo.setAlignment(Qt.AlignCenter)
        mark = paths.asset(branding.LOGO_MARK_FILE)
        if mark.is_file():
            pm = QPixmap(str(mark))
            if not pm.isNull():
                self.logo.setPixmap(pm.scaled(36, 36, Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))
            else:
                self.logo.setText(branding.LOGO_MONOGRAM)
        else:
            self.logo.setText(branding.LOGO_MONOGRAM)
        lay.addWidget(self.logo)

        self.brand_box = QWidget()
        bb = QVBoxLayout(self.brand_box)
        bb.setContentsMargins(0, 0, 0, 0)
        bb.setSpacing(0)
        name = QLabel(branding.APP_SHORT_NAME)
        name.setObjectName("BrandName")
        tag = QLabel(branding.COMPANY_NAME)
        tag.setObjectName("BrandTagline")
        bb.addWidget(name)
        bb.addWidget(tag)
        lay.addWidget(self.brand_box, 1)

        self.toggle_btn = QPushButton("«")
        self.toggle_btn.setObjectName("HeaderIconButton")
        self.toggle_btn.setFixedSize(26, 26)
        self.toggle_btn.setToolTip("Collapse the sidebar  (Ctrl+B)")
        self.toggle_btn.clicked.connect(self.toggle)
        lay.addWidget(self.toggle_btn)
        return header

    def _build_search(self) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(12, 10, 12, 6)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchBox")
        self.search_box.setPlaceholderText("Search tools…   Ctrl+K")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter)
        lay.addWidget(self.search_box)
        self.search_holder = holder
        return holder

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        lay = QVBoxLayout(footer)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(2)
        self.version_label = QLabel(branding.version_string())
        self.version_label.setObjectName("BrandTagline")
        lay.addWidget(self.version_label)
        self.privacy_label = QLabel("Processed locally on this computer")
        self.privacy_label.setObjectName("BrandTagline")
        self.privacy_label.setWordWrap(True)
        lay.addWidget(self.privacy_label)
        self.footer = footer
        return footer

    # -- navigation --------------------------------------------------------
    def _build_nav(self) -> None:
        if self._favourites:
            label = QLabel("FAVOURITES")
            label.setObjectName("NavSection")
            self.nav_layout.addWidget(label)
            self._section_labels.append(label)
            for key in self._favourites:
                spec = BY_KEY.get(key)
                if spec is None:
                    continue
                btn = self._nav_button(spec)
                self.nav_layout.addWidget(btn)
                self._buttons[f"fav:{spec.key}"] = btn
                self.group.addButton(btn)

        for section, mods in by_section().items():
            label = QLabel(section)
            label.setObjectName("NavSection")
            self.nav_layout.addWidget(label)
            self._section_labels.append(label)

            for spec in mods:
                btn = self._nav_button(spec)
                self.nav_layout.addWidget(btn)
                self._buttons[spec.key] = btn
                self.group.addButton(btn)
        self.nav_layout.addStretch(1)

    def _nav_button(self, spec: ModuleSpec) -> QPushButton:
        btn = QPushButton(f"  {spec.icon}    {spec.title}")
        btn.setObjectName("NavItem")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(f"{spec.title}\n{spec.subtitle}")
        btn.setProperty("module_key", spec.key)
        btn.setProperty("icon_text", spec.icon)
        btn.setProperty("full_text", f"  {spec.icon}    {spec.title}")
        btn.clicked.connect(lambda _=False, k=spec.key: self.navigate.emit(k))
        btn.setContextMenuPolicy(Qt.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, k=spec.key, b=btn: self._nav_menu(k, b, pos))
        return btn

    def _nav_menu(self, key: str, button: QPushButton, pos) -> None:
        menu = QMenu(self)
        if key in self._favourites:
            menu.addAction("Unpin from Favourites",
                           lambda: self.toggle_favourite(key))
        else:
            menu.addAction("Pin to Favourites", lambda: self.toggle_favourite(key))
        if self._favourites:
            menu.addSeparator()
            menu.addAction("Clear all favourites", self.clear_favourites)
        menu.exec(button.mapToGlobal(pos))

    # -- favourites --------------------------------------------------------
    def toggle_favourite(self, key: str) -> None:
        if key in self._favourites:
            self._favourites.remove(key)
        else:
            self._favourites.append(key)
        config.set("favourite_tools", self._favourites)
        self._rebuild_nav()

    def clear_favourites(self) -> None:
        self._favourites = []
        config.set("favourite_tools", [])
        self._rebuild_nav()

    def is_favourite(self, key: str) -> bool:
        return key in self._favourites

    def _rebuild_nav(self) -> None:
        current = self._current
        while self.nav_layout.count():
            item = self.nav_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._buttons.clear()
        self._section_labels.clear()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._build_nav()
        if self._collapsed:
            self.set_collapsed(True)
        if current:
            self.set_current(current)

    def set_current(self, key: str) -> None:
        self._current = key
        btn = self._buttons.get(key) or self._buttons.get(f"fav:{key}")
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def current(self) -> str:
        return self._current

    # -- search ------------------------------------------------------------
    def _filter(self, text: str) -> None:
        query = text.strip()
        if not query:
            for b in self._buttons.values():
                b.setVisible(True)
            for l in self._section_labels:
                l.setVisible(not self._collapsed)
            return
        allowed = {m.key for m in search(query)}
        for key, b in self._buttons.items():
            b.setVisible(key.removeprefix("fav:") in allowed)
        for label in self._section_labels:
            label.setVisible(False)

    def focus_search(self) -> None:
        if self._collapsed:
            self.toggle()
        self.search_box.setFocus()
        self.search_box.selectAll()

    def first_search_result(self) -> str:
        for key, b in self._buttons.items():
            if b.isVisible():
                return key
        return ""

    # -- collapse ----------------------------------------------------------
    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH)
        self.brand_box.setVisible(not collapsed)
        self.search_holder.setVisible(not collapsed)
        self.footer.setVisible(not collapsed)
        self.toggle_btn.setText("»" if collapsed else "«")
        self.toggle_btn.setToolTip(
            "Expand the sidebar  (Ctrl+B)" if collapsed else "Collapse the sidebar  (Ctrl+B)")

        for label in self._section_labels:
            label.setVisible(not collapsed)
        for b in self._buttons.values():
            if collapsed:
                b.setText(f"  {b.property('icon_text')}")
                b.setToolTip(b.property("full_text").strip())
            else:
                b.setText(b.property("full_text"))
            b.setVisible(True)

        config.set("sidebar_collapsed", collapsed)
        self.collapsed_changed.emit(collapsed)

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed
