"""Unlock PDF — removes protection when the correct password is supplied."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QLineEdit

from app import db
from app.core import security, utils
from app.core.pdfbase import needs_password
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class UnlockPage(SingleFileToolPage):
    TITLE = "Unlock PDF"
    SUBTITLE = "Remove protection using the correct password"
    ACTION = "Remove protection"
    OUTPUT_SUFFIX = "unlocked"
    HISTORY_MODULE = "Unlock"

    def build(self) -> None:
        self.scroll.add(C.Banner(
            "PDF Master Toolkit does not guess, crack or bypass PDF passwords. "
            "This tool removes protection from documents you can already open — "
            "either because you know the password, or because the file has only "
            "permission restrictions that you are entitled to change.", "info"))

        self.add_input_section()

        self.status_card = C.Card("Protection on this file")
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_card.add(self.status_label)
        self.status_card.setVisible(False)
        self.scroll.add(self.status_card)

        card = C.Card("Password")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.returnPressed.connect(self._on_run_clicked)
        card.add(C.FormRow(
            "Document password", self.pw,
            "Leave blank if the file opens without a password and only has "
            "permission restrictions."))
        self.show_pw = C.check("Show password", False)
        self.show_pw.toggled.connect(
            lambda on: self.pw.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        card.add(self.show_pw)
        self.scroll.add(card)

        self.add_output_section()
        self.add_run_section()

    def set_source(self, path) -> None:
        # the base class prompts for a password to *open* the file; here the
        # password is the whole point, so handle it ourselves
        p = Path(path)
        if not p.is_file():
            return
        from app.core.pdf_ops import inspect
        self.source = p
        self.password = None
        self.info = inspect(p)
        if self.drop is not None:
            self.drop.setVisible(False)
        self.file_summary.setText(self._summary_text())
        self.file_summary.setVisible(True)
        self.change_btn.setVisible(True)
        self.preview_btn.setVisible(False)
        if self.output is not None:
            self.output.set_source(p, self.OUTPUT_SUFFIX)
        self.on_source_changed()

    def _summary_text(self) -> str:
        """
        An encrypted file reports 0 pages until it is opened, so the inherited
        summary would read "0 pages" and look broken. Say what is actually true.
        """
        if not self.source:
            return ""
        if self.info is not None and self.info.encrypted:
            return (f"🔒  {self.source.name}     ·     "
                    f"{utils.human_size(utils.file_size(self.source))}     ·     "
                    f"encrypted — page count shown after unlocking")
        return super()._summary_text()

    def on_source_changed(self) -> None:
        if not self.source:
            return
        info = security.inspect_security(self.source)
        self.status_card.setVisible(True)

        if info.error and not info.encrypted:
            self.status_label.setText(f"This file could not be read: {info.error}")
            return

        if info.needs_password:
            self.status_label.setText(
                "🔒  This document needs a password to open. Enter it below.")
            self.pw.setEnabled(True)
            self.pw.setFocus()
        elif info.encrypted or info.restricted:
            restricted = ", ".join(info.restricted) or "none"
            self.status_label.setText(
                f"This document opens without a password but has permission "
                f"restrictions in place.\nRestricted: {restricted}.\n\n"
                f"You can remove them by leaving the password box empty and "
                f"pressing “Remove protection”.")
        else:
            self.status_label.setText(
                "This document is not protected — there is nothing to remove. "
                "Running the tool anyway simply saves a clean copy.")

    def validate(self) -> str:
        """
        Deliberately does NOT call super().

        The base validator rejects any file whose inspection reported an error,
        and an encrypted PDF always reports "Password protected" — which is
        precisely the file this screen exists to open. Inheriting that rule
        made Unlock refuse every locked document.
        """
        if not self.source:
            return "Choose a PDF first."
        if not self.source.is_file():
            return f"{self.source.name} no longer exists."

        info = security.inspect_security(self.source)
        if info.needs_password and not self.pw.text():
            return "Enter the document's password."
        if info.error and not info.encrypted:
            return info.error
        return ""

    def start(self) -> None:
        target = self.output.resolved(self.source, "unlocked")
        self.run_job(security.unlock, self.source, self.pw.text(), target,
                     name=f"Unlocking {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, [result.output] if result.output else [],
                         "", result.warnings)
        self.record("Unlock", self.source, result.output, result.pages)
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast("Protection removed")
