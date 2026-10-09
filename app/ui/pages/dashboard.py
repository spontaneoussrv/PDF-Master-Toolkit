"""Dashboard — statistics, a large drop target, quick actions and recent work."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from app import branding, config, db
from app.core import deps, utils
from app.ui import components as C
from app.ui.pages import BY_KEY, QUICK_ACTIONS, get
from app.ui.pages.base import BasePage


class DashboardPage(BasePage):
    TITLE = "Dashboard"
    SUBTITLE = "Your PDF workspace at a glance"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self._stat_cards: dict[str, C.StatCard] = {}

    # ------------------------------------------------------------------ ui
    def build(self) -> None:
        self.scroll.add(self._build_hero())
        self.scroll.add_layout(self._build_stats())
        self.scroll.add(self._build_dropzone())
        self.scroll.add(self._build_quick_actions())
        self.scroll.add_layout(self._build_bottom_row())
        self.scroll.add_stretch()

    # -- hero --------------------------------------------------------------
    def _build_hero(self) -> QWidget:
        hero = QFrame()
        hero.setObjectName("Hero")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(20)

        col = QVBoxLayout()
        col.setSpacing(6)
        greeting = QLabel(self._greeting())
        greeting.setObjectName("HeroTitle")
        col.addWidget(greeting)

        sub = QLabel(
            f"{branding.APP_TAGLINE} — {len([m for m in BY_KEY.values() if not m.hidden])} "
            f"tools, all running on this computer.")
        sub.setObjectName("HeroText")
        sub.setWordWrap(True)
        col.addWidget(sub)
        lay.addLayout(col, 1)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        open_btn = QPushButton("Open a PDF")
        open_btn.setMinimumWidth(160)
        open_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.96);color:#1B2440;"
            "border:none;border-radius:8px;padding:10px 18px;font-weight:700;}"
            "QPushButton:hover{background:#FFFFFF;}")
        open_btn.clicked.connect(lambda: self.window_ref.open_document()
                                 if self.window_ref else None)
        actions.addWidget(open_btn)

        batch_btn = QPushButton("Batch processing")
        batch_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.16);color:#FFFFFF;"
            "border:1px solid rgba(255,255,255,0.34);border-radius:8px;"
            "padding:10px 18px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.26);}")
        batch_btn.clicked.connect(lambda: self.go("batch"))
        actions.addWidget(batch_btn)
        lay.addLayout(actions)
        return hero

    def _greeting(self) -> str:
        hour = datetime.now().hour
        part = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        return f"{part} — what are we working on?"

    # -- stats -------------------------------------------------------------
    def _build_stats(self):
        grid = QGridLayout()
        grid.setSpacing(14)

        cards = [
            ("pdfs_processed", "PDFs Processed", "📄", branding.BRAND_ACCENT, "history"),
            ("ocr_documents", "OCR Documents", "◍", branding.BRAND_INFO, "ocr"),
            ("files_converted", "Files Converted", "⇄", branding.BRAND_ACCENT_ALT, "pdf2word"),
            ("bytes_saved", "Space Saved", "⤓", branding.BRAND_SUCCESS, "compress"),
        ]
        for i, (key, label, icon, accent, target) in enumerate(cards):
            card = C.StatCard(label, "0", icon, accent)
            card.clicked.connect(lambda k=target: self.go(k))
            self._stat_cards[key] = card
            grid.addWidget(card, 0, i)
        return grid

    def _refresh_stats(self) -> None:
        stats = db.get_stats()
        for key, card in self._stat_cards.items():
            value = stats.get(key, 0.0)
            if key == "bytes_saved":
                card.set_value(utils.human_size(value))
                if value:
                    card.set_delta("saved")
            else:
                card.set_value(f"{int(value):,}")
        pages = stats.get("pages_processed", 0)
        if pages and "pdfs_processed" in self._stat_cards:
            self._stat_cards["pdfs_processed"].set_delta(f"{int(pages):,} pages")
        ocr_pages = stats.get("ocr_pages", 0)
        if ocr_pages and "ocr_documents" in self._stat_cards:
            self._stat_cards["ocr_documents"].set_delta(f"{int(ocr_pages):,} pages")

    # -- drop zone ---------------------------------------------------------
    def _build_dropzone(self) -> QWidget:
        card = C.Card()
        self.drop = C.DropZone(
            "Drop PDF files here",
            hint="or click to browse — then choose what to do with them",
            extensions=(".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
                        ".bmp", ".webp", ".docx", ".xlsx", ".csv"),
            icon="⬇",
        )
        self.drop.files_dropped.connect(self.handle_files)
        card.add(self.drop)

        self.pending_label = QLabel("")
        self.pending_label.setObjectName("Subtle")
        self.pending_label.setWordWrap(True)
        self.pending_label.setVisible(False)
        card.add(self.pending_label)

        self.suggestion_row = QHBoxLayout()
        self.suggestion_row.setSpacing(8)
        card.add_layout(self.suggestion_row)
        return card

    def handle_files(self, paths: Sequence[str]) -> None:
        """Show contextual next steps for whatever was dropped."""
        self._pending = [str(p) for p in paths]
        pdfs = [p for p in self._pending if p.lower().endswith(".pdf")]
        imgs = [p for p in self._pending if Path(p).suffix.lower() in
                (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")]
        docs = [p for p in self._pending if Path(p).suffix.lower() in (".docx", ".doc")]
        sheets = [p for p in self._pending if Path(p).suffix.lower() in
                  (".xlsx", ".xls", ".csv")]

        names = ", ".join(Path(p).name for p in self._pending[:3])
        more = f" and {len(self._pending) - 3} more" if len(self._pending) > 3 else ""
        self.pending_label.setText(f"Ready: {names}{more} — choose what to do:")
        self.pending_label.setVisible(True)

        while self.suggestion_row.count():
            item = self.suggestion_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        suggestions: list[tuple[str, str]] = []
        if len(pdfs) > 1:
            suggestions += [("merge", "Merge into one PDF"), ("batch", "Batch process")]
        if pdfs:
            suggestions += [("ocr", "Run OCR"), ("compress", "Compress"),
                            ("organize", "Organise pages"), ("viewer", "Open in viewer")]
        if imgs:
            suggestions.append(("img2pdf", "Build a PDF from images"))
        if docs:
            suggestions.append(("word2pdf", "Convert Word to PDF"))
        if sheets:
            suggestions.append(("excel2pdf", "Convert to PDF"))

        for key, label in suggestions[:6]:
            btn = QPushButton(label)
            btn.setObjectName("ChipButton")
            btn.clicked.connect(lambda _=False, k=key: self._dispatch(k))
            self.suggestion_row.addWidget(btn)
        self.suggestion_row.addStretch(1)

    def _dispatch(self, key: str) -> None:
        files = getattr(self, "_pending", [])
        if key == "viewer" and files:
            self.go("viewer", files[0])
            return
        self.go(key)
        if self.window_ref is not None:
            page = self.window_ref.current_page()
            if page is not None and hasattr(page, "handle_files") and files:
                QTimer.singleShot(60, lambda: page.handle_files(files))

    # -- quick actions -----------------------------------------------------
    def _build_quick_actions(self) -> QWidget:
        card = C.Card("Quick actions", "Jump straight to the tools you use most")
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, key in enumerate(QUICK_ACTIONS):
            spec = get(key)
            if spec is None:
                continue
            btn = QPushButton(f"  {spec.icon}    {spec.title}\n        {spec.subtitle}")
            btn.setObjectName("QuickAction")
            btn.setToolTip(spec.subtitle)
            btn.clicked.connect(lambda _=False, k=key: self.go(k))
            grid.addWidget(btn, i // 3, i % 3)
        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        return card

    # -- bottom row --------------------------------------------------------
    def _build_bottom_row(self):
        self.recent_card = C.Card("Recent activity", "Files you have worked on")
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(4)
        self.recent_card.add_layout(self.recent_box)

        clear = QPushButton("Clear")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._clear_recent)
        self.recent_card.add_header_widget(clear)

        self.status_card = C.Card("System status", "What this installation can do")
        self.status_box = QVBoxLayout()
        self.status_box.setSpacing(6)
        self.status_card.add_layout(self.status_box)

        return C.columns(self.recent_card, self.status_card, stretches=[3, 2])

    def _refresh_recent(self) -> None:
        while self.recent_box.count():
            item = self.recent_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not db.history_enabled():
            lbl = QLabel("History is switched off in Settings → Privacy.")
            lbl.setObjectName("Subtle")
            self.recent_box.addWidget(lbl)
            return

        rows = db.get_history(limit=7)
        if not rows:
            lbl = QLabel("Nothing yet — process a file and it will appear here.")
            lbl.setObjectName("Subtle")
            self.recent_box.addWidget(lbl)
            return

        for row in rows:
            self.recent_box.addWidget(self._recent_row(row))

        more = QPushButton("View all history →")
        more.setObjectName("Ghost")
        more.clicked.connect(lambda: self.go("history"))
        self.recent_box.addWidget(more, 0, Qt.AlignLeft)

    def _recent_row(self, row) -> QWidget:
        holder = QFrame()
        holder.setObjectName("CardFlat")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        icon = QLabel("📄")
        lay.addWidget(icon)

        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(utils.elide(row["input_name"] or row["output_name"] or "—", 44))
        name.setToolTip(row["input_path"] or "")
        col.addWidget(name)

        when = row["ts"][:16].replace("T", "  ")
        detail = QLabel(f"{row['operation']}  ·  {when}")
        detail.setObjectName("Subtle")
        col.addWidget(detail)
        lay.addLayout(col, 1)

        if row["status"] != "success":
            lay.addWidget(C.Badge("Failed", "danger"))

        out = row["output_path"]
        if out and Path(out).exists():
            btn = QPushButton("Open")
            btn.setObjectName("Ghost")
            btn.clicked.connect(lambda _=False, p=out: utils.open_path(p))
            lay.addWidget(btn)
        return holder

    def _clear_recent(self) -> None:
        db.clear_recent()
        self._refresh_recent()
        self.toast("Recent files cleared")

    def _refresh_status(self) -> None:
        while self.status_box.count():
            item = self.status_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        caps = deps.capabilities()
        rows = [
            ("OCR engine", caps["ocr"], "ocr"),
            ("Table extraction", caps["table_extraction"], "pdf2excel"),
            ("Word conversion", caps["pdf_to_word_layout"], "pdf2word"),
            ("Office conversion",
             caps["word_to_pdf_office"] or caps["word_to_pdf_libreoffice"], "word2pdf"),
            ("Scanner", caps["scanner"], "scan"),
            ("Maximum compression", caps["ghostscript_compression"], "compress"),
        ]
        for name, ok, target in rows:
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color:{branding.BRAND_SUCCESS if ok else '#98A2B3'};font-size:11px;")
            row.addWidget(dot)
            lbl = QLabel(name)
            row.addWidget(lbl, 1)
            badge = C.Badge("Ready" if ok else "Unavailable", "success" if ok else "")
            row.addWidget(badge)
            holder = QWidget()
            holder.setLayout(row)
            self.status_box.addWidget(holder)

        if not caps["ocr"]:
            hint = QPushButton("Set up OCR →")
            hint.setObjectName("Ghost")
            hint.clicked.connect(lambda: self.go("settings"))
            self.status_box.addWidget(hint, 0, Qt.AlignLeft)

        note = QLabel(branding.PRIVACY_STATEMENT)
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        self.status_box.addWidget(note)

    # -- lifecycle ---------------------------------------------------------
    def on_show(self) -> None:
        self._refresh_stats()
        self._refresh_recent()
        self._refresh_status()
