"""
Base classes for every tool screen.

:class:`BasePage` gives each module its header and scroll host.
:class:`ToolPage` adds the standard machinery every tool needs: a run bar, a
log, a result panel, job submission, cancellation, error reporting and history
recording — so an individual tool only has to describe its own options and say
what function to run.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from app import config, db
from app.core import utils
from app.core.jobs import Job
from app.ui import components as C


class BasePage(QWidget):
    """Header + scrollable body. Every sidebar destination is one of these."""

    #: overridden by subclasses; used by the sidebar and window title
    TITLE = "Page"
    SUBTITLE = ""
    ICON = "•"

    navigate = Signal(str, object)          # module key, payload
    status_message = Signal(str)

    def __init__(self, window=None, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self.scroll = C.ScrollArea()
        self._root.addWidget(self.scroll, 1)
        self._built = False

    # -- lifecycle ---------------------------------------------------------
    def ensure_built(self) -> None:
        """Build the UI the first time the page is shown, not at startup."""
        if self._built:
            return
        self._built = True
        try:
            self.build()
        except Exception as e:                       # pragma: no cover
            self.scroll.add(C.Banner(
                f"This screen could not be loaded: {e}", "danger"))
            traceback.print_exc()

    def build(self) -> None:
        """Subclasses assemble their content here."""

    def on_show(self) -> None:
        """Called every time the page becomes visible."""

    def on_hide(self) -> None:
        """Called when navigating away."""

    def handle_files(self, paths: Sequence[str]) -> None:
        """Called when files are dropped on the window while this page is open."""

    def can_close(self) -> bool:
        return True

    # -- helpers -----------------------------------------------------------
    @property
    def jobs(self):
        return self.window_ref.jobs if self.window_ref else None

    def toast(self, message: str, kind: str = "success") -> None:
        target = self.window_ref or self
        C.toast(target, message, kind)

    def go(self, module_key: str, payload: Any = None) -> None:
        self.navigate.emit(module_key, payload)


class ToolPage(BasePage):
    """A page that runs one background operation."""

    ACTION = "Start"
    OUTPUT_SUFFIX = "processed"
    SHOW_PAUSE = False
    HISTORY_MODULE = ""

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.run_bar: C.RunBar | None = None
        self.log: C.LogView | None = None
        self.result_panel: C.ResultPanel | None = None
        self._job: Job | None = None
        self._started_at = 0

    # -- standard footer ---------------------------------------------------
    def add_run_section(self, action: str | None = None,
                        with_log: bool = True) -> C.RunBar:
        # Guard against a widget attribute shadowing one of the hooks the run
        # bar depends on. Assigning e.g. `self.start = QSpinBox()` in build()
        # would otherwise fail only when the user pressed the button, with a
        # confusing "object is not callable". Fail loudly here instead.
        for hook in ("start", "validate", "on_success", "cancel", "run_job"):
            if not callable(getattr(self, hook, None)):
                raise TypeError(
                    f"{type(self).__name__}.{hook} has been overwritten by a "
                    f"widget. Rename that attribute — it shadows a method this "
                    f"page needs to run its job."
                )

        self.result_panel = C.ResultPanel()
        self.result_panel.setVisible(False)
        self.scroll.add(self.result_panel)

        if with_log:
            log_card = C.Card("Activity", shadow=False, flat=True)
            self.log = C.LogView()
            log_card.add(self.log)
            log_card.setVisible(False)
            self._log_card = log_card
            self.scroll.add(log_card)

        self.run_bar = C.RunBar(action or self.ACTION, show_pause=self.SHOW_PAUSE)
        self.run_bar.run_clicked.connect(self._on_run_clicked)
        self.run_bar.cancel_clicked.connect(self.cancel)
        self.run_bar.pause_clicked.connect(self.toggle_pause)
        self.scroll.add(self.run_bar)
        return self.run_bar

    # -- running -----------------------------------------------------------
    def _on_run_clicked(self) -> None:
        try:
            problem = self.validate()
        except Exception as e:
            problem = str(e)
        if problem:
            self.set_status(problem, "error")
            self.toast(problem, "error")
            return
        try:
            self.start()
        except Exception as e:
            self.report_failure(str(e), traceback.format_exc())

    def validate(self) -> str:
        """Return an error message, or '' when the inputs are usable."""
        return ""

    def start(self) -> None:
        """Subclasses submit their job here, usually via :meth:`run_job`."""

    def run_job(self, fn: Callable, *args, name: str = "",
                on_done: Callable[[Any], None] | None = None,
                on_item: Callable[[Any], None] | None = None,
                **kwargs) -> Job:
        """Submit a worker function and wire it to this page's UI."""
        if self._job is not None:
            self.set_status("Another operation is still running on this screen.", "warning")
            return self._job

        job = Job(fn, *args, name=name or self.TITLE, **kwargs)
        self._job = job
        self._on_done_cb = on_done

        if self.log is not None:
            self.log.clear_log()
            self._log_card.setVisible(True)
        if self.result_panel is not None:
            self.result_panel.setVisible(False)
        if self.run_bar is not None:
            self.run_bar.set_running(True)
            self.run_bar.set_status("Starting…")

        job.signals.progress.connect(self._on_progress)
        job.signals.log.connect(self._on_log)
        if on_item is not None:
            job.signals.item.connect(on_item)
        job.signals.finished.connect(self._on_finished)
        job.signals.failed.connect(self._on_failed)
        job.signals.cancelled.connect(self._on_cancelled)
        job.signals.done.connect(self._on_done)

        if self.jobs is not None:
            self.jobs.submit(job)
        else:                                       # standalone/testing
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().start(job)
        return job

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.set_status("Cancelling…", "warning")

    def toggle_pause(self) -> None:
        if self._job is None or self.run_bar is None:
            return
        if self._job.is_paused:
            self._job.resume()
            self.run_bar.pause_btn.setText("Pause")
            self.set_status("Resumed")
        else:
            self._job.pause()
            self.run_bar.pause_btn.setText("Resume")
            self.set_status("Paused", "warning")

    @property
    def is_running(self) -> bool:
        return self._job is not None

    # -- signal handlers ---------------------------------------------------
    def _on_progress(self, value: int, message: str) -> None:
        if self.run_bar is not None:
            self.run_bar.set_progress(value, message)

    def _on_log(self, message: str, level: str) -> None:
        if self.log is not None:
            self.log.append_line(message, level)

    def _on_finished(self, result: Any) -> None:
        try:
            self.on_success(result)
        except Exception as e:
            self.report_failure(str(e), traceback.format_exc())

    def _on_failed(self, message: str, tb: str) -> None:
        self.report_failure(message, tb)

    def _on_cancelled(self) -> None:
        self.set_status("Cancelled — nothing was changed", "warning")
        self.on_cancelled()

    def _on_done(self) -> None:
        self._job = None
        if self.run_bar is not None:
            self.run_bar.set_running(False)

    # -- overridable outcomes ----------------------------------------------
    def on_success(self, result: Any) -> None:
        self.set_status("Done", "success")

    def on_cancelled(self) -> None:
        pass

    def report_failure(self, message: str, tb: str = "") -> None:
        self.set_status(message.split("\n")[0], "error")
        if self.log is not None:
            self._log_card.setVisible(True)
            self.log.append_line(message, "error")
        box = QMessageBox(self.window_ref or self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Could not finish")
        box.setText(message.split("\n")[0])
        rest = "\n".join(message.split("\n")[1:]).strip()
        if rest:
            box.setInformativeText(rest)
        if tb:
            box.setDetailedText(tb)
        box.exec()

    # -- conveniences ------------------------------------------------------
    def set_status(self, message: str, kind: str = "") -> None:
        if self.run_bar is not None:
            self.run_bar.set_status(message, kind)
        self.status_message.emit(message)

    def show_result(self, summary: str, paths: Sequence[Path] | None = None,
                    detail: str = "", warnings: Sequence[str] = ()) -> None:
        if self.result_panel is not None:
            extra = "\n".join(warnings)
            self.result_panel.show_result(summary, paths,
                                          (detail + ("\n\n" if detail and extra else "") + extra).strip())
        for w in warnings:
            if self.log is not None:
                self.log.append_line(w, "warning")
        self.set_status(summary, "success")

    def finish_output(self, paths: Sequence[Path], panel: C.OutputPanel | None = None) -> None:
        """Honour the 'open when finished' and 'show folder' checkboxes."""
        if not paths:
            return
        first = Path(paths[0])
        if panel is not None:
            if panel.open_after.isChecked():
                utils.open_path(first)
            if panel.reveal_after.isChecked():
                utils.reveal_in_explorer(first)
        elif config.get_bool("open_output_after", True):
            utils.open_path(first)

    def record(self, operation: str, source: Path | None, output: Path | None,
               pages: int = 0, detail: str = "", status: str = "success") -> None:
        db.add_history(
            operation=operation,
            module=self.HISTORY_MODULE or self.TITLE,
            input_path=source,
            output_path=output,
            input_size=utils.file_size(source) if source else 0,
            output_size=utils.file_size(output) if output else 0,
            pages=pages,
            duration_ms=self._job.elapsed_ms if self._job else 0,
            status=status,
            detail=detail,
        )

    def confirm(self, title: str, text: str, informative: str = "",
                destructive: bool = False) -> bool:
        box = QMessageBox(self.window_ref or self)
        box.setIcon(QMessageBox.Warning if destructive else QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        if informative:
            box.setInformativeText(informative)
        yes = box.addButton("Continue", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        if destructive:
            yes.setObjectName("DangerButton")
        box.exec()
        return box.clickedButton() is yes


class SingleFileToolPage(ToolPage):
    """
    The most common shape: pick one PDF, set options, produce one output.

    Provides the drop zone, the file summary, an :class:`~app.ui.components.OutputPanel`
    and password handling, so subclasses implement only :meth:`build_options`
    and :meth:`start`.
    """

    EXTENSIONS: tuple[str, ...] = (".pdf",)
    DROP_TEXT = "Drop a PDF here"
    OUTPUT_EXT = ".pdf"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.source: Path | None = None
        self.password: str | None = None
        self.info = None
        self.drop: C.DropZone | None = None
        self.file_card: C.Card | None = None
        self.output: C.OutputPanel | None = None

    # -- input card --------------------------------------------------------
    def add_input_section(self, subtitle: str = "") -> None:
        card = C.Card("Source document", subtitle)
        self.drop = C.DropZone(self.DROP_TEXT, extensions=self.EXTENSIONS)
        self.drop.files_dropped.connect(lambda paths: self.set_source(paths[0]))
        card.add(self.drop)

        self.file_summary = QLabel("")
        self.file_summary.setObjectName("Subtle")
        self.file_summary.setWordWrap(True)
        self.file_summary.setVisible(False)
        card.add(self.file_summary)

        row = QHBoxLayout()
        self.change_btn = QPushButton("Choose a different file")
        self.change_btn.setObjectName("Ghost")
        self.change_btn.clicked.connect(lambda: self.drop.browse())
        self.change_btn.setVisible(False)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setObjectName("Ghost")
        self.preview_btn.clicked.connect(self._preview)
        self.preview_btn.setVisible(False)
        row.addWidget(self.change_btn)
        row.addWidget(self.preview_btn)
        row.addStretch(1)
        card.add_layout(row)

        self.file_card = card
        self.scroll.add(card)

    def add_output_section(self, folder_mode: bool = False) -> C.OutputPanel:
        self.output = C.OutputPanel(self.OUTPUT_SUFFIX, self.OUTPUT_EXT,
                                    folder_mode=folder_mode)
        self.scroll.add(self.output)
        return self.output

    # -- source handling ---------------------------------------------------
    def set_source(self, path: str | Path) -> None:
        from app.core.pdf_ops import inspect
        from app.core.pdfbase import needs_password

        p = Path(path)
        if not p.is_file():
            return

        password = None
        if p.suffix.lower() == ".pdf" and needs_password(p):
            password = self._ask_password(p)
            if password is None:
                return

        self.source = p
        self.password = password
        if p.suffix.lower() == ".pdf":
            self.info = inspect(p, password)
        else:
            self.info = None

        if self.drop is not None:
            self.drop.setVisible(False)
        if hasattr(self, "file_summary"):
            self.file_summary.setText(self._summary_text())
            self.file_summary.setVisible(True)
            self.change_btn.setVisible(True)
            self.preview_btn.setVisible(p.suffix.lower() == ".pdf")
        if self.output is not None:
            self.output.set_source(p, self.OUTPUT_SUFFIX)
        self.on_source_changed()

    def _summary_text(self) -> str:
        if not self.source:
            return ""
        bits = [f"📄  {self.source.name}"]
        if self.info and self.info.ok:
            bits.append(f"{self.info.pages} pages")
            if self.info.page_size:
                bits.append(self.info.page_size)
        bits.append(utils.human_size(utils.file_size(self.source)))
        if self.info and self.info.encrypted:
            bits.append("encrypted")
        return "     ·     ".join(bits)

    def _ask_password(self, path: Path) -> str | None:
        from PySide6.QtWidgets import QInputDialog, QLineEdit as QLE

        for attempt in range(3):
            prompt = (f"'{path.name}' is password protected.\n"
                      "Enter the password to open it:")
            if attempt:
                prompt = f"That password was not accepted.\n\n{prompt}"
            pw, ok = QInputDialog.getText(self, "Password required", prompt, QLE.Password)
            if not ok:
                return None
            from app.core.pdfbase import open_pdf
            try:
                doc = open_pdf(path, pw)
                doc.close()
                return pw
            except Exception:
                continue
        QMessageBox.information(self, "Cannot open",
                                "The password was not accepted. PDF Master Toolkit "
                                "does not attempt to recover PDF passwords.")
        return None

    def on_source_changed(self) -> None:
        """Hook for subclasses — update page-range fields, previews, etc."""

    def _preview(self) -> None:
        if self.source:
            self.go("viewer", str(self.source))

    def handle_files(self, paths: Sequence[str]) -> None:
        for p in paths:
            if Path(p).suffix.lower() in self.EXTENSIONS:
                self.set_source(p)
                return

    def validate(self) -> str:
        if not self.source:
            return "Choose a PDF first."
        if self.info is not None and not self.info.ok and self.info.error:
            return self.info.error
        return ""

    @property
    def page_count(self) -> int:
        return self.info.pages if self.info else 0
