"""Protect PDF."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QGridLayout, QLabel, QLineEdit,
                               QPushButton, QWidget)

from app import db
from app.core import security
from app.core.pdfbase import PERMISSION_LABELS
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class ProtectPage(SingleFileToolPage):
    TITLE = "Protect PDF"
    SUBTITLE = "Encrypt with a password and set permissions"
    ACTION = "Protect PDF"
    OUTPUT_SUFFIX = "protected"
    HISTORY_MODULE = "Protect"

    def build(self) -> None:
        self.add_input_section()

        self.current_card = C.Card("Current protection")
        self.current_label = QLabel("")
        self.current_label.setWordWrap(True)
        self.current_card.add(self.current_label)
        self.current_card.setVisible(False)
        self.scroll.add(self.current_card)

        pw_card = C.Card("Passwords", "Set one or both. They can be different.")
        self.open_pw = QLineEdit()
        self.open_pw.setEchoMode(QLineEdit.Password)
        self.open_pw.textChanged.connect(self._update_strength)
        pw_card.add(C.FormRow(
            "Open password", self.open_pw,
            "Required to view the document. This is what actually encrypts the file."))

        self.open_pw2 = QLineEdit()
        self.open_pw2.setEchoMode(QLineEdit.Password)
        self.open_pw2.textChanged.connect(self._update_strength)
        pw_card.add(C.FormRow("Confirm", self.open_pw2))

        self.strength = QLabel("")
        self.strength.setObjectName("Subtle")
        pw_card.add(self.strength)

        self.owner_pw = QLineEdit()
        self.owner_pw.setEchoMode(QLineEdit.Password)
        pw_card.add(C.FormRow(
            "Permissions password", self.owner_pw,
            "Required to change the restrictions below. Leave blank to reuse the "
            "open password."))

        self.show_pw = C.check("Show passwords", False)
        self.show_pw.toggled.connect(self._toggle_echo)
        pw_card.add(self.show_pw)

        gen = QPushButton("Generate a strong password")
        gen.setObjectName("Ghost")
        gen.clicked.connect(self._generate)
        pw_card.add(gen)
        self.scroll.add(pw_card)

        enc_card = C.Card("Encryption")
        self.method = C.combo(
            [(k, v[1]) for k, v in security.ENCRYPTION_METHODS.items()],
            security.DEFAULT_METHOD)
        enc_card.add(C.FormRow("Method", self.method))
        self.scroll.add(enc_card)

        perm_card = C.Card("What readers may do",
                           "Unticked actions are blocked in readers that honour "
                           "PDF permissions.")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.perms: dict[str, QCheckBox] = {}
        defaults = {"print": True, "print_hq": True, "copy": False, "modify": False,
                    "annotate": False, "form": True, "assemble": False,
                    "accessibility": True}
        for i, key in enumerate(security.PERMISSION_ORDER):
            cb = C.check(PERMISSION_LABELS[key], defaults.get(key, True))
            if key == "accessibility":
                cb.setToolTip("Leave this on so screen readers can read the document "
                              "aloud for people with visual impairments.")
            self.perms[key] = cb
            grid.addWidget(cb, i // 2, i % 2)
        holder = QWidget()
        holder.setLayout(grid)
        perm_card.add(holder)
        perm_card.add(C.Banner(security.PERMISSION_DISCLAIMER, "warning"))
        self.scroll.add(perm_card)

        self.add_output_section()
        self.add_run_section()

    def _toggle_echo(self, show: bool) -> None:
        mode = QLineEdit.Normal if show else QLineEdit.Password
        for e in (self.open_pw, self.open_pw2, self.owner_pw):
            e.setEchoMode(mode)

    def _generate(self) -> None:
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
        pw = "".join(secrets.choice(alphabet) for _ in range(18))
        self.open_pw.setText(pw)
        self.open_pw2.setText(pw)
        self.show_pw.setChecked(True)
        C.copy_to_clipboard(pw)
        self.toast("Password generated and copied to the clipboard — save it somewhere safe")

    def _update_strength(self) -> None:
        pw = self.open_pw.text()
        if not pw:
            self.strength.setText("")
            return
        score = 0
        if len(pw) >= 8:
            score += 1
        if len(pw) >= 14:
            score += 1
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw):
            score += 1
        if any(c.isdigit() for c in pw):
            score += 1
        if any(not c.isalnum() for c in pw):
            score += 1
        labels = ["Very weak", "Weak", "Fair", "Good", "Strong", "Very strong"]
        mismatch = (self.open_pw2.text() and self.open_pw2.text() != pw)
        text = f"Strength: {labels[score]}"
        if mismatch:
            text += "     ⚠  The two passwords do not match."
        self.strength.setText(text)

    def on_source_changed(self) -> None:
        info = security.inspect_security(self.source, self.password)
        if info.encrypted:
            restricted = ", ".join(info.restricted) or "none"
            self.current_label.setText(
                f"This document is already encrypted. Restricted actions: {restricted}.\n"
                f"Protecting it again replaces the existing passwords and permissions.")
            self.current_card.setVisible(True)
        else:
            self.current_card.setVisible(False)

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        if not self.open_pw.text() and not self.owner_pw.text():
            return "Enter an open password, a permissions password, or both."
        if self.open_pw.text() != self.open_pw2.text():
            return "The two open passwords do not match."
        return ""

    def start(self) -> None:
        if self.open_pw.text() and len(self.open_pw.text()) < 6:
            if not self.confirm(
                "That password is short",
                "A password of fewer than six characters offers little protection.",
                "Continue anyway, or go back and choose a longer one."):
                return

        opts = security.ProtectOptions(
            user_password=self.open_pw.text(),
            owner_password=self.owner_pw.text(),
            method=self.method.currentData(),
            allow={k: cb.isChecked() for k, cb in self.perms.items()},
        )
        target = self.output.resolved(self.source, "protected")
        self.run_job(security.protect, self.source, target, opts, self.password,
                     name=f"Protecting {self.source.name}")

    def on_success(self, result) -> None:
        detail = ("Keep the password somewhere safe. There is no way to recover a "
                  "PDF password — not with this application, and not with any other.")
        self.show_result(result.message, [result.output] if result.output else [],
                         detail, result.warnings)
        self.record("Protect", self.source, result.output, result.pages,
                    self.method.currentData())
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast("PDF protected")
