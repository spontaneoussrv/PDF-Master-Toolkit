"""About — version, support, privacy and third-party attribution."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from app import branding, db, paths
from app.core import deps, utils
from app.ui import components as C
from app.ui.pages import MODULES
from app.ui.pages.base import BasePage


class AboutPage(BasePage):
    TITLE = "About"
    SUBTITLE = "Version, support and privacy"

    def build(self) -> None:
        self.scroll.add(self._hero())
        self.scroll.add_layout(C.columns(self._support_card(), self._system_card()))
        self.scroll.add(self._privacy_card())
        self.scroll.add(self._features_card())
        self.scroll.add(self._legal_card())
        self.scroll.add_stretch()

    # -- hero --------------------------------------------------------------
    def _hero(self) -> QWidget:
        hero = QFrame()
        hero.setObjectName("Hero")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(28, 26, 28, 26)
        lay.setSpacing(22)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        # Prefer the full SheetClub lockup; fall back to the square mark, then
        # to a monogram tile, so the page always renders.
        for name, w, h in ((branding.LOGO_FILE, 250, 96),
                           (branding.LOGO_MARK_FILE, 84, 84)):
            f = paths.asset(name)
            if not f.is_file():
                continue
            pm = QPixmap(str(f))
            if pm.isNull():
                continue
            logo.setPixmap(pm.scaled(w, h, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation))
            break
        if logo.pixmap() is None or logo.pixmap().isNull():
            logo.setFixedSize(72, 72)
            logo.setText(branding.LOGO_MONOGRAM)
            logo.setStyleSheet(
                "background:rgba(255,255,255,0.22);border-radius:16px;"
                "color:#FFFFFF;font-size:26px;font-weight:800;")
        lay.addWidget(logo, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(5)
        name = QLabel(branding.APP_NAME)
        name.setObjectName("HeroTitle")
        col.addWidget(name)

        tagline = QLabel(branding.APP_TAGLINE)
        tagline.setObjectName("HeroText")
        col.addWidget(tagline)

        version = QLabel(f"{branding.version_string()}   ·   by {branding.COMPANY_NAME}")
        version.setObjectName("HeroText")
        col.addWidget(version)

        copyright_ = QLabel(branding.COPYRIGHT)
        copyright_.setObjectName("HeroText")
        col.addWidget(copyright_)
        lay.addLayout(col, 1)
        return hero

    # -- support -----------------------------------------------------------
    def _support_card(self) -> C.Card:
        card = C.Card("Support", branding.SUPPORT_NOTE)

        grid = QGridLayout()
        grid.setSpacing(8)
        rows = [
            ("Email", branding.SUPPORT_EMAIL, f"mailto:{branding.SUPPORT_EMAIL}"),
            ("Phone", branding.SUPPORT_PHONE, ""),
            ("Support site", branding.SUPPORT_URL, branding.SUPPORT_URL),
            ("Documentation", branding.DOCS_URL, branding.DOCS_URL),
            ("Website", branding.COMPANY_WEBSITE, branding.COMPANY_WEBSITE),
            ("Hours", branding.SUPPORT_HOURS, ""),
            ("Address", branding.COMPANY_ADDRESS, ""),
        ]
        row_index = 0
        for label, value, link in rows:
            if not value:
                continue
            lb = QLabel(label)
            lb.setObjectName("Subtle")
            grid.addWidget(lb, row_index, 0)
            if link:
                val = QLabel(f'<a href="{link}" style="color:{branding.BRAND_ACCENT};'
                             f'text-decoration:none;">{value}</a>')
                val.setOpenExternalLinks(True)
            else:
                val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextBrowserInteraction)
            val.setWordWrap(True)
            grid.addWidget(val, row_index, 1)
            row_index += 1

        if row_index == 0:
            grid.addWidget(QLabel("Support details have not been configured yet."), 0, 0)

        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)

        row = QHBoxLayout()
        diag = QPushButton("Copy the diagnostics report")
        diag.setObjectName("Primary")
        diag.clicked.connect(self._copy_diagnostics)
        row.addWidget(diag)
        logs = QPushButton("Open the log folder")
        logs.setObjectName("Ghost")
        logs.clicked.connect(lambda: utils.open_path(paths.logs_dir()))
        row.addWidget(logs)
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _copy_diagnostics(self) -> None:
        C.copy_to_clipboard(deps.report())
        self.toast("Diagnostics copied — paste them into your support email")

    # -- system ------------------------------------------------------------
    def _system_card(self) -> C.Card:
        card = C.Card("This installation")
        grid = QGridLayout()
        grid.setSpacing(8)

        stats = db.get_stats()
        rows = [
            ("Version", f"{branding.APP_VERSION} (build {branding.APP_BUILD})"),
            ("Licence mode", branding.LICENSE_MODE.title()),
            ("Tools available", str(len([m for m in MODULES if not m.hidden]))),
            ("Windows", f"{platform.system()} {platform.release()}"),
            ("Python", sys.version.split()[0]),
            ("Installed at", str(paths.app_root())),
            ("Your data", str(paths.user_data_dir())),
            ("Documents processed", f"{int(stats.get('pdfs_processed', 0)):,}"),
            ("Space saved", utils.human_size(stats.get("bytes_saved", 0))),
        ]
        for i, (label, value) in enumerate(rows):
            lb = QLabel(label)
            lb.setObjectName("Subtle")
            grid.addWidget(lb, i, 0)
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(val, i, 1)

        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        return card

    # -- privacy -----------------------------------------------------------
    def _privacy_card(self) -> C.Card:
        card = C.Card("Privacy")
        banner = C.Banner(branding.PRIVACY_STATEMENT, "success")
        card.add(banner)

        detail = QLabel(branding.PRIVACY_DETAIL)
        detail.setWordWrap(True)
        card.add(detail)

        row = QHBoxLayout()
        if branding.PRIVACY_URL:
            b = QPushButton("Read the privacy policy")
            b.setObjectName("Ghost")
            b.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(branding.PRIVACY_URL)))
            row.addWidget(b)
        settings = QPushButton("Privacy settings")
        settings.setObjectName("Ghost")
        settings.clicked.connect(lambda: self.go("settings"))
        row.addWidget(settings)
        row.addStretch(1)
        card.add_layout(row)
        return card

    # -- features ----------------------------------------------------------
    def _features_card(self) -> C.Card:
        from app.ui.pages import by_section

        card = C.Card("What is included",
                      f"{len([m for m in MODULES if not m.hidden])} tools across "
                      f"{len(by_section())} categories.")
        grid = QGridLayout()
        grid.setSpacing(6)
        sections = by_section()
        col = 0
        for section, mods in sections.items():
            box = QVBoxLayout()
            box.setSpacing(3)
            head = QLabel(section)
            head.setObjectName("SectionTitle")
            box.addWidget(head)
            for m in mods:
                lb = QLabel(f"{m.icon}   {m.title}")
                lb.setObjectName("Subtle")
                box.addWidget(lb)
            box.addStretch(1)
            holder = QWidget()
            holder.setLayout(box)
            grid.addWidget(holder, col // 4, col % 4)
            col += 1
        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        return card

    # -- legal -------------------------------------------------------------
    def _legal_card(self) -> C.Card:
        card = C.Card("Open-source components",
                      "This product is built on the following projects.")
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.addWidget(self._head("Component"), 0, 0)
        grid.addWidget(self._head("Licence"), 0, 1)
        grid.addWidget(self._head("Project"), 0, 2)

        for i, (name, licence, url) in enumerate(branding.THIRD_PARTY, start=1):
            grid.addWidget(QLabel(name), i, 0)
            lic = QLabel(licence)
            lic.setObjectName("Subtle")
            grid.addWidget(lic, i, 1)
            link = QLabel(f'<a href="{url}" style="color:{branding.BRAND_ACCENT};'
                          f'text-decoration:none;">{url}</a>')
            link.setOpenExternalLinks(True)
            link.setObjectName("Subtle")
            grid.addWidget(link, i, 2)

        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        card.add(C.Banner(branding.LICENSING_NOTICE, "warning"))
        return card

    def _head(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("SectionTitle")
        return lb
