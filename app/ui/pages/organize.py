"""Organise Pages — the visual page manager."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from app import db
from app.core import utils
from app.core.pdf_ops import apply_page_plan
from app.core.pdfbase import needs_password
from app.ui import components as C
from app.ui.organizer import PageOrganizer
from app.ui.pages.base import ToolPage


class OrganizePage(ToolPage):
    TITLE = "Organise Pages"
    SUBTITLE = "Reorder, rotate, delete and insert pages visually"
    ACTION = "Save organised PDF"
    HISTORY_MODULE = "Organise"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.source: Path | None = None
        self.password: str | None = None

    def build(self) -> None:
        # full-height layout rather than a scroll page
        self._root.removeWidget(self.scroll)
        self.scroll.setParent(None)

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)

        self.drop = C.DropZone("Drop a PDF here to organise its pages",
                               hint="or click to browse")
        self.drop.files_dropped.connect(lambda p: self.load(p[0]))
        lay.addWidget(self.drop)

        self.organizer = PageOrganizer()
        self.organizer.setVisible(False)
        self.organizer.plan_changed.connect(self._on_plan_changed)
        self.organizer.page_activated.connect(self._preview_page)
        lay.addWidget(self.organizer, 1)

        self.footer = QWidget()
        flay = QHBoxLayout(self.footer)
        flay.setContentsMargins(0, 0, 0, 0)
        flay.setSpacing(10)

        self.file_label = QLabel("")
        self.file_label.setObjectName("Subtle")
        flay.addWidget(self.file_label, 1)

        change = QPushButton("Open a different PDF")
        change.setObjectName("Ghost")
        change.clicked.connect(lambda: self.drop.browse())
        flay.addWidget(change)

        self.save_as_check = C.check("Save as a new file", True,
                                     "Uncheck to overwrite the original — you will be "
                                     "asked to confirm.")
        flay.addWidget(self.save_as_check)

        self.save_btn = QPushButton("Save organised PDF")
        self.save_btn.setObjectName("PrimaryLarge")
        self.save_btn.clicked.connect(self._on_run_clicked)
        self.save_btn.setEnabled(False)
        flay.addWidget(self.save_btn)

        self.footer.setVisible(False)
        lay.addWidget(self.footer)
        self._root.addWidget(container, 1)

    # -- loading -----------------------------------------------------------
    def handle_files(self, paths: Sequence[str]) -> None:
        for p in paths:
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
            for _ in range(3):
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
                    break
                except Exception:
                    continue
            else:
                return

        if not self.organizer.load(p, password):
            return
        self.source = p
        self.password = password
        self.drop.setVisible(False)
        self.organizer.setVisible(True)
        self.footer.setVisible(True)
        self.file_label.setText(
            f"📄  {p.name}      {self.organizer.page_count} pages      "
            f"{utils.human_size(utils.file_size(p))}")
        self.save_btn.setEnabled(False)
        self.status_message.emit(f"Opened {p.name}")

    def _on_plan_changed(self) -> None:
        self.save_btn.setEnabled(self.organizer.has_changes())

    def _preview_page(self, index: int) -> None:
        if self.source:
            self.go("viewer", str(self.source))

    # -- saving ------------------------------------------------------------
    def validate(self) -> str:
        if not self.source:
            return "Open a PDF first."
        if not self.organizer.slots:
            return "The document would have no pages."
        if not self.organizer.has_changes():
            return "Nothing has changed yet."
        return ""

    def start(self) -> None:
        if self.save_as_check.isChecked():
            target = utils.suggest_output(self.source, "organised", ".pdf")
        else:
            if not self.confirm(
                "Overwrite the original?",
                f"{self.source.name} will be replaced with the organised version.",
                "This cannot be undone. Uncheck 'Save as a new file' only when you are "
                "sure you no longer need the original layout.",
                destructive=True,
            ):
                return
            target = utils.unique_path(
                self.source.parent / f"{self.source.stem}_tmp_organised.pdf")
        self._overwrite = not self.save_as_check.isChecked()
        self._target = target

        self.run_job(apply_page_plan, self.source, self.organizer.build_plan(),
                     target, self.password,
                     name=f"Organising {self.source.name}")

    def on_success(self, result) -> None:
        out = result.output
        if getattr(self, "_overwrite", False) and out is not None:
            try:
                import shutil
                shutil.move(str(out), str(self.source))
                out = self.source
                result.outputs = [self.source]
            except OSError as e:
                self.report_failure(
                    f"The organised file was created but the original could not be "
                    f"replaced ({e}). Your result is at {out}.")
                return

        self.show_result(f"Saved {result.pages} page(s)", result.outputs,
                         f"Saved as {out.name}" if out else "", result.warnings)
        self.record("Organise pages", self.source, out, result.pages)
        db.bump_stat("pdfs_processed")
        db.bump_stat("pages_processed", result.pages)
        self.toast("Organised PDF saved")
        if out:
            utils.open_path(out)
        # reload so further edits start from the saved state
        if out:
            self.load(out)

    def can_close(self) -> bool:
        if self.organizer is None or not self.organizer.has_changes():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Unsaved page changes")
        box.setText("You have reordered pages without saving.")
        box.setInformativeText("Closing now discards those changes. "
                               "The original file has not been modified.")
        discard = box.addButton("Discard changes", QMessageBox.AcceptRole)
        box.addButton("Go back", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is discard
