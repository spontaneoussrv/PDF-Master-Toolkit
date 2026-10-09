"""PDF Metadata — view, edit and strip document properties."""

from __future__ import annotations

from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QWidget)

from app import db
from app.core import metadata as meta_mod
from app.core import utils
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class MetadataPage(SingleFileToolPage):
    TITLE = "PDF Metadata"
    SUBTITLE = "View, edit or strip document properties"
    ACTION = "Save metadata"
    OUTPUT_SUFFIX = "metadata"
    HISTORY_MODULE = "Metadata"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self._meta: meta_mod.Metadata | None = None
        self.fields: dict[str, QLineEdit] = {}

    def build(self) -> None:
        self.add_input_section()

        self.info_card = C.Card("Document information")
        self.info_grid = QGridLayout()
        self.info_grid.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self.info_grid)
        self.info_card.add(holder)
        self.info_card.setVisible(False)
        self.scroll.add(self.info_card)

        edit_card = C.Card("Editable properties")
        for key in ("title", "author", "subject", "keywords", "creator", "producer"):
            edit = QLineEdit()
            self.fields[key] = edit
            edit_card.add(C.FormRow(meta_mod.FIELD_LABELS[key], edit))

        self.created = QLineEdit()
        self.created.setPlaceholderText("D:20260312143000  or  2026-03-12")
        self.modified = QLineEdit()
        self.modified.setPlaceholderText("Left blank, this is set to now")
        self.fields["creationDate"] = self.created
        self.fields["modDate"] = self.modified
        edit_card.add(C.FormRow("Created", self.created))
        edit_card.add(C.FormRow("Modified", self.modified))

        row = QHBoxLayout()
        for text, slot in [("Reload from file", self._reload),
                           ("Clear all boxes", self._clear_fields)]:
            b = QPushButton(text)
            b.setObjectName("Ghost")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        edit_card.add_layout(row)
        self.scroll.add(edit_card)

        strip_card = C.Card("Remove document metadata",
                            "Clears the properties above and the embedded XMP packet.")
        self.clear_xmp = C.check("Remove the XMP metadata packet", True)
        self.clear_bookmarks = C.check("Also remove bookmarks", False)
        self.clear_attachments = C.check("Also remove attachments", False)
        self.clear_annotations = C.check("Also remove annotations and comments", False)
        for cb in (self.clear_xmp, self.clear_bookmarks, self.clear_attachments,
                   self.clear_annotations):
            strip_card.add(cb)

        self.strip_btn = QPushButton("Remove document metadata")
        self.strip_btn.setObjectName("DangerGhost")
        self.strip_btn.clicked.connect(self._strip)
        strip_card.add(self.strip_btn)
        strip_card.add(C.Banner(meta_mod.METADATA_DISCLAIMER, "warning"))
        self.scroll.add(strip_card)

        self.add_output_section()
        self.add_run_section()

    # -- loading -----------------------------------------------------------
    def on_source_changed(self) -> None:
        self._reload()

    def _reload(self) -> None:
        if not self.source:
            return
        try:
            self._meta = meta_mod.read_metadata(self.source, self.password)
        except Exception as e:
            self.set_status(f"Could not read metadata: {e}", "error")
            return

        for key, edit in self.fields.items():
            edit.setText(getattr(self._meta, key, "") or "")

        while self.info_grid.count():
            item = self.info_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        m = self._meta
        rows = [
            ("Pages", f"{m.pages}"),
            ("File size", utils.human_size(m.file_size)),
            ("PDF version", m.pdf_version or "—"),
            ("First page size", m.page_size or "—"),
            ("Encrypted", "Yes" if m.encrypted else "No"),
            ("XMP metadata", "Present" if m.has_xmp else "None"),
            ("Form fields", "Yes" if m.has_forms else "No"),
            ("Attachments", "Yes" if m.has_attachments else "No"),
            ("Created", meta_mod.humanize_date(m.creationDate)),
            ("Modified", meta_mod.humanize_date(m.modDate)),
        ]
        for i, (label, value) in enumerate(rows):
            lb = QLabel(label)
            lb.setObjectName("Subtle")
            self.info_grid.addWidget(lb, i // 2, (i % 2) * 2)
            vl = QLabel(value)
            self.info_grid.addWidget(vl, i // 2, (i % 2) * 2 + 1)
        self.info_card.setVisible(True)

    def _clear_fields(self) -> None:
        for edit in self.fields.values():
            edit.clear()

    # -- actions -----------------------------------------------------------
    def start(self) -> None:
        values = {k: e.text() for k, e in self.fields.items()
                  if k not in ("creationDate", "modDate")}
        if self.created.text().strip():
            values["creationDate"] = self.created.text().strip()
        target = self.output.resolved(self.source, "metadata")
        self.run_job(meta_mod.write_metadata, self.source, values, target,
                     self.password, name=f"Updating {self.source.name}")

    def _strip(self) -> None:
        if not self.source:
            self.toast("Choose a PDF first", "warning")
            return
        if not self.confirm(
            "Remove document metadata?",
            "The title, author, subject, keywords and creator details will be "
            "cleared from the copy that is saved.",
            meta_mod.METADATA_DISCLAIMER,
        ):
            return
        target = self.output.resolved(self.source, "clean")
        self.run_job(meta_mod.remove_metadata, self.source, target, self.password,
                     clear_xmp=self.clear_xmp.isChecked(),
                     clear_bookmarks=self.clear_bookmarks.isChecked(),
                     clear_attachments=self.clear_attachments.isChecked(),
                     clear_annotations=self.clear_annotations.isChecked(),
                     name=f"Cleaning {self.source.name}")

    def on_success(self, result) -> None:
        from pathlib import Path
        out = Path(result) if not hasattr(result, "output") else result.output
        self.show_result("Saved", [out] if out else [],
                         f"Saved as {out.name}" if out else "")
        self.record("Metadata", self.source, out)
        db.bump_stat("pdfs_processed")
        self.finish_output([out] if out else [], self.output)
        self.toast("Metadata saved")
