"""Sign PDF — visual signatures and certificate signing."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from app import db, paths
from app.core import annotate, deps, utils
from app.core.pdfbase import needs_password
from app.ui import components as C
from app.ui.pages.base import ToolPage
from app.ui.signature import SignatureDialog, SignaturePlacer


class SignPage(ToolPage):
    TITLE = "Sign PDF"
    SUBTITLE = "Place a signature, or sign with a certificate"
    ACTION = "Apply signature"
    HISTORY_MODULE = "Sign"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.source: Path | None = None
        self.password: str | None = None
        self.signature_path: Path | None = None
        self._placements: list[annotate.SignaturePlacement] = []
        self._current = None

    def build(self) -> None:
        self._root.removeWidget(self.scroll)
        self.scroll.setParent(None)

        outer = QWidget()
        lay = QHBoxLayout(outer)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ---- left: page canvas -------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(10)
        self.drop = C.DropZone("Drop a PDF here to sign it", hint="or click to browse",
                               compact=True)
        self.drop.files_dropped.connect(lambda p: self.load(p[0]))
        left.addWidget(self.drop)

        self.placer = SignaturePlacer()
        self.placer.setVisible(False)
        self.placer.page_loaded.connect(self._on_page_loaded)
        left.addWidget(self.placer, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.setObjectName("Ghost")
        self.prev_btn.clicked.connect(
            lambda: self.placer.set_page(self.placer.page_index - 1))
        self.page_label = QLabel("")
        self.page_label.setObjectName("Subtle")
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.setObjectName("Ghost")
        self.next_btn.clicked.connect(
            lambda: self.placer.set_page(self.placer.page_index + 1))
        self.add_here_btn = QPushButton("Place here")
        self.add_here_btn.setObjectName("Primary")
        self.add_here_btn.setToolTip("Record the signature at its current position "
                                     "on this page")
        self.add_here_btn.clicked.connect(self._add_placement)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label, 1)
        nav.addWidget(self.add_here_btn)
        nav.addWidget(self.next_btn)
        self.nav = QWidget()
        self.nav.setLayout(nav)
        self.nav.setVisible(False)
        left.addWidget(self.nav)
        lay.addLayout(left, 3)

        # ---- right: controls ---------------------------------------------
        side = C.ScrollArea(margins=(0, 0, 0, 0), spacing=14)
        side.setMaximumWidth(400)
        side.setMinimumWidth(340)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._visual_tab(), "Visual signature")
        self.tabs.addTab(self._certificate_tab(), "Certificate")
        side.add(self.tabs)

        self.output = C.OutputPanel("signed", ".pdf")
        side.add(self.output)

        self.result_panel = C.ResultPanel()
        side.add(self.result_panel)

        self.run_bar = C.RunBar("Apply signature")
        self.run_bar.run_clicked.connect(self._on_run_clicked)
        self.run_bar.cancel_clicked.connect(self.cancel)
        self.run_bar.set_enabled(False)
        side.add(self.run_bar)
        side.add_stretch()
        lay.addWidget(side, 2)
        self._root.addWidget(outer, 1)

        self._refresh_signatures()

    # -- tabs --------------------------------------------------------------
    def _visual_tab(self) -> QWidget:
        holder = C.ScrollArea(margins=(2, 8, 2, 8), spacing=12)

        card = C.Card("Your signatures")
        self.sig_list = QListWidget()
        self.sig_list.setIconSize(QSize(150, 46))
        self.sig_list.setMaximumHeight(170)
        self.sig_list.currentRowChanged.connect(self._on_signature_selected)
        card.add(self.sig_list)

        row = QHBoxLayout()
        new_btn = QPushButton("Create a signature")
        new_btn.setObjectName("Primary")
        new_btn.clicked.connect(self._create_signature)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("Ghost")
        del_btn.clicked.connect(self._delete_signature)
        row.addWidget(new_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        card.add_layout(row)
        holder.add(card)

        placed = C.Card("Where it will appear",
                        "Drag the box on the page, then press “Place here”. "
                        "You can add it to several pages.")
        self.placement_list = QListWidget()
        self.placement_list.setMaximumHeight(120)
        placed.add(self.placement_list)
        prow = QHBoxLayout()
        remove = QPushButton("Remove selected")
        remove.setObjectName("Ghost")
        remove.clicked.connect(self._remove_placement)
        clear = QPushButton("Clear all")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._clear_placements)
        prow.addWidget(remove)
        prow.addWidget(clear)
        prow.addStretch(1)
        placed.add_layout(prow)
        holder.add(placed)

        extras = C.Card("Extras")
        self.add_date = C.check("Print the date under the signature", False)
        extras.add(self.add_date)
        holder.add(extras)

        holder.add(C.Banner(annotate.SIGNATURE_DISCLAIMER, "info"))
        holder.add_stretch()
        return holder

    def _certificate_tab(self) -> QWidget:
        holder = C.ScrollArea(margins=(2, 8, 2, 8), spacing=12)

        if not deps.has_pyhanko():
            holder.add(C.Banner(
                "Certificate signing needs the pyHanko component, which is not "
                "included in this build. Visual signatures on the other tab work "
                "fully offline, but they are not cryptographic signatures.",
                "warning"))

        card = C.Card("Digital certificate",
                      "A cryptographic signature proves who signed the document and "
                      "shows if it has been altered since.")
        self.pfx = C.PathPicker("open", "Certificates (*.pfx *.p12)",
                                "Choose your .pfx or .p12 certificate")
        card.add(C.FormRow("Certificate file", self.pfx, label_width=120))

        from PySide6.QtWidgets import QLineEdit
        self.pfx_password = QLineEdit()
        self.pfx_password.setEchoMode(QLineEdit.Password)
        card.add(C.FormRow("Certificate password", self.pfx_password, label_width=120))

        self.reason = QLineEdit()
        self.reason.setPlaceholderText("I approve this document")
        card.add(C.FormRow("Reason", self.reason, label_width=120))
        self.location = QLineEdit()
        card.add(C.FormRow("Location", self.location, label_width=120))
        self.contact = QLineEdit()
        card.add(C.FormRow("Contact", self.contact, label_width=120))

        self.visible_sig = C.check("Show a signature box on the page", True)
        card.add(self.visible_sig)
        holder.add(card)

        holder.add(C.Banner(
            "The certificate never leaves this computer. Signing happens locally "
            "and the file is written straight to disk.", "info"))
        holder.add_stretch()
        return holder

    # -- signatures --------------------------------------------------------
    def _refresh_signatures(self) -> None:
        self.sig_list.clear()
        for row in db.get_signatures():
            item = QListWidgetItem(f"  {row['name']}")
            item.setData(Qt.UserRole, row["id"])
            path = Path(row["file"])
            if path.is_file():
                pm = QPixmap(str(path))
                if not pm.isNull():
                    item.setIcon(pm.scaled(148, 44, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))
            item.setToolTip(str(path))
            item.setData(33, str(path))
            self.sig_list.addItem(item)
        if self.sig_list.count():
            self.sig_list.setCurrentRow(0)
        else:
            self.signature_path = None
            self.placer.set_signature(None)

    def _on_signature_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.sig_list.item(row)
        path = item.data(33)
        if path:
            self.signature_path = Path(path)
            self.placer.set_signature(self.signature_path)

    def _create_signature(self) -> None:
        dialog = SignatureDialog(self)
        if dialog.exec() and dialog.result_path:
            self._refresh_signatures()
            for i in range(self.sig_list.count()):
                if self.sig_list.item(i).data(33) == str(dialog.result_path):
                    self.sig_list.setCurrentRow(i)
                    break
            self.toast("Signature saved")

    def _delete_signature(self) -> None:
        item = self.sig_list.currentItem()
        if item is None:
            return
        sig_id = item.data(Qt.UserRole)
        if sig_id is None:
            return
        if not self.confirm("Delete this signature?",
                            f"“{item.text().strip()}” will be removed from your saved "
                            f"signatures.", destructive=True):
            return
        db.delete_signature(int(sig_id))
        self._refresh_signatures()
        self.toast("Signature deleted")

    # -- placements --------------------------------------------------------
    def _add_placement(self) -> None:
        if self.source is None:
            return
        rect = self.placer.rect_points()
        page = self.placer.page_index + 1
        self._placements.append(annotate.SignaturePlacement(page, rect))
        self.placement_list.addItem(
            f"Page {page}   ·   {rect[2] - rect[0]:.0f} × {rect[3] - rect[1]:.0f} pt")
        self.run_bar.set_enabled(True)
        self.set_status(f"Signature will be placed on {len(self._placements)} page(s)")

    def _remove_placement(self) -> None:
        row = self.placement_list.currentRow()
        if row < 0:
            return
        self.placement_list.takeItem(row)
        del self._placements[row]
        self.run_bar.set_enabled(bool(self._placements) or
                                 self.tabs.currentIndex() == 1)

    def _clear_placements(self) -> None:
        self.placement_list.clear()
        self._placements.clear()
        self.run_bar.set_enabled(self.tabs.currentIndex() == 1)

    # -- loading -----------------------------------------------------------
    def handle_files(self, paths_: Sequence[str]) -> None:
        for p in paths_:
            if str(p).lower().endswith(".pdf"):
                self.load(p)
                return

    def receive(self, payload) -> None:
        if isinstance(payload, (str, Path)):
            self.load(payload)

    def load(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            return
        password = None
        if needs_password(p):
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            from app.core.pdfbase import open_pdf
            pw, ok = QInputDialog.getText(
                self, "Password required",
                f"'{p.name}' is password protected.\nEnter the password:",
                QLineEdit.Password)
            if not ok:
                return
            try:
                d = open_pdf(p, pw)
                d.close()
                password = pw
            except Exception:
                self.toast("That password was not accepted", "error")
                return

        if not self.placer.load(p, password):
            self.toast("This PDF could not be opened", "error")
            return
        self.source = p
        self.password = password
        self.drop.setVisible(False)
        self.placer.setVisible(True)
        self.nav.setVisible(True)
        self.output.set_source(p, "signed")
        self._clear_placements()
        if self.signature_path:
            self.placer.set_signature(self.signature_path)

    def _on_page_loaded(self, index: int, count: int) -> None:
        self.page_label.setText(f"Page {index + 1} of {count}")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < count - 1)

    # -- run ---------------------------------------------------------------
    def validate(self) -> str:
        if not self.source:
            return "Open a PDF first."
        if self.tabs.currentIndex() == 1:
            if not deps.has_pyhanko():
                return "Certificate signing is not available in this build."
            if not self.pfx.path():
                return "Choose your certificate file."
            return ""
        if not self.signature_path:
            return "Create or choose a signature first."
        if not self._placements:
            return "Position the signature and press “Place here”."
        return ""

    def start(self) -> None:
        target = self.output.resolved(self.source, "signed")
        if self.tabs.currentIndex() == 1:
            opts = annotate.CertificateSignOptions(
                pfx_path=self.pfx.path(),
                pfx_password=self.pfx_password.text(),
                reason=self.reason.text(),
                location=self.location.text(),
                contact=self.contact.text(),
                page=self.placer.page_index + 1,
                rect=self.placer.rect_points() if self.visible_sig.isChecked() else None,
                visible=self.visible_sig.isChecked(),
            )
            self.run_job(annotate.sign_certificate, self.source, target, opts,
                         name=f"Signing {self.source.name}")
            return

        opts = annotate.VisualSignOptions(
            image_path=self.signature_path,
            placements=list(self._placements),
            add_date=self.add_date.isChecked(),
        )
        self.run_job(annotate.sign_visual, self.source, target, opts, self.password,
                     name=f"Signing {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, [result.output] if result.output else [],
                         "", result.warnings)
        self.record("Sign", self.source, result.output, result.pages,
                    "certificate" if self.tabs.currentIndex() == 1 else "visual")
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast("Signature applied")
