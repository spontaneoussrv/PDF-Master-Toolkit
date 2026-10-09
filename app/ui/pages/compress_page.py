"""Compress PDF."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app import config, db
from app.core import compress as comp
from app.core import deps, utils
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class CompressPage(SingleFileToolPage):
    TITLE = "Compress PDF"
    SUBTITLE = "Reduce file size for email and storage"
    ACTION = "Compress PDF"
    OUTPUT_SUFFIX = "compressed"
    HISTORY_MODULE = "Compress"

    def build(self) -> None:
        self.add_input_section()

        profile_card = C.Card("Compression level")
        self.profile = C.SegmentedControl(
            [(k, comp.PROFILES[k].label) for k in comp.PROFILE_ORDER],
            config.get("compress_profile", "balanced"))
        self.profile.changed.connect(self._on_profile)
        profile_card.add(self.profile)

        self.profile_desc = C.muted(comp.PROFILES["balanced"].description)
        profile_card.add(self.profile_desc)

        self.custom_holder = QWidget()
        cl = QVBoxLayout(self.custom_holder)
        cl.setContentsMargins(0, 8, 0, 0)
        cl.setSpacing(10)
        self.dpi = C.spin(150, 36, 600, " DPI")
        self.dpi.valueChanged.connect(self._estimate_soon)
        cl.addWidget(C.FormRow("Image resolution", self.dpi,
                               "Images above this are downsampled. 150 suits screens; "
                               "300 suits printing."))
        self.quality = C.spin(72, 15, 96, "%")
        self.quality.valueChanged.connect(self._estimate_soon)
        cl.addWidget(C.FormRow("JPEG quality", self.quality))
        self.custom_holder.setVisible(False)
        profile_card.add(self.custom_holder)
        self.scroll.add(profile_card)

        adv = C.Card("What to optimise")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.downsample = C.check("Downsample oversized images", True)
        self.recompress = C.check("Re-encode images", True)
        self.grayscale = C.check("Convert images to grayscale", False,
                                 "Large saving on colour scans of black-and-white "
                                 "documents.")
        self.dedupe = C.check("Remove duplicate resources", True)
        self.clean = C.check("Rebuild the document structure", True)
        self.subset = C.check("Subset embedded fonts", True,
                              "Keeps only the characters actually used.")
        self.strip_meta = C.check("Remove metadata", False)
        self.strip_annots = C.check("Remove annotations and comments", False)
        self.strip_forms = C.check("Remove attachments", False)
        self.linearize = C.check("Optimise for fast web viewing", False)
        for i, cb in enumerate((self.downsample, self.recompress, self.grayscale,
                                self.dedupe, self.clean, self.subset,
                                self.strip_meta, self.strip_annots,
                                self.strip_forms, self.linearize)):
            cb.toggled.connect(self._estimate_soon)
            grid.addWidget(cb, i // 2, i % 2)
        holder = QWidget()
        holder.setLayout(grid)
        adv.add(holder)

        self.backend = C.combo(self._backend_options(), "auto")
        adv.add(C.FormRow("Engine", self.backend))
        self.scroll.add(adv)

        est = C.Card("Estimate", "A quick forecast from a sample of the pages. The "
                                 "real figure appears once compression finishes.")
        row = QHBoxLayout()
        row.setSpacing(24)
        self.orig_label = self._metric("Original size", "—")
        self.est_label = self._metric("Estimated size", "—")
        self.save_label = self._metric("Estimated saving", "—")
        for w in (self.orig_label, self.est_label, self.save_label):
            row.addWidget(w)
        row.addStretch(1)
        est.add_layout(row)
        self.est_note = C.muted("")
        est.add(self.est_note)
        self.scroll.add(est)

        self.add_output_section()
        self.add_run_section()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._estimate)

    def _backend_options(self) -> list[tuple]:
        opts = [("auto", "Automatic — best available")]
        opts.append(("builtin", "Built-in (safe, keeps text sharp)"))
        if deps.has_ghostscript():
            opts.append(("ghostscript", "Ghostscript (strongest on scans)"))
        return opts

    def _metric(self, label: str, value: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        v = QLabel(value)
        v.setObjectName("StatValue")
        l = QLabel(label)
        l.setObjectName("StatLabel")
        lay.addWidget(v)
        lay.addWidget(l)
        w.setProperty("value_label", v)
        return w

    @staticmethod
    def _set_metric(widget: QWidget, value: str) -> None:
        widget.property("value_label").setText(value)

    # -- state -------------------------------------------------------------
    def _on_profile(self, key: str) -> None:
        profile = comp.PROFILES.get(key)
        if profile:
            self.profile_desc.setText(profile.description)
            if key != "custom":
                self.dpi.blockSignals(True)
                self.quality.blockSignals(True)
                self.dpi.setValue(profile.dpi)
                self.quality.setValue(profile.jpeg_quality)
                self.grayscale.setChecked(profile.grayscale)
                self.dpi.blockSignals(False)
                self.quality.blockSignals(False)
        self.custom_holder.setVisible(key == "custom")
        self._estimate_soon()

    def on_source_changed(self) -> None:
        if self.source:
            self._set_metric(self.orig_label,
                             utils.human_size(utils.file_size(self.source)))
        self._estimate_soon()

    def _estimate_soon(self) -> None:
        self._timer.start()

    def _estimate(self) -> None:
        if not self.source:
            return
        info = comp.estimate(self.source, self._options())
        self._set_metric(self.orig_label, utils.human_size(info["original"]))
        self._set_metric(self.est_label, utils.human_size(info["estimated"]))
        saved = info["original"] - info["estimated"]
        pct = (saved / info["original"] * 100) if info["original"] else 0
        self._set_metric(self.save_label, f"{max(0, pct):.0f}%")
        if info["image_bytes"] < info["original"] * 0.15:
            self.est_note.setText(
                "Most of this file is text and vectors rather than images, so the "
                "saving will be modest — there is little to compress without "
                "damaging quality.")
        else:
            self.est_note.setText(
                f"About {utils.human_size(info['image_bytes'])} of this file is "
                f"image data, which is where the saving comes from.")

    def _options(self) -> comp.CompressOptions:
        key = self.profile.value()
        opts = comp.CompressOptions.from_profile(key)
        if key == "custom":
            opts.dpi = self.dpi.value()
            opts.jpeg_quality = self.quality.value()
        else:
            opts.dpi = self.dpi.value()
            opts.jpeg_quality = self.quality.value()
        opts.grayscale_images = self.grayscale.isChecked()
        opts.downsample_images = self.downsample.isChecked()
        opts.recompress_images = self.recompress.isChecked()
        opts.remove_duplicates = self.dedupe.isChecked()
        opts.clean_structure = self.clean.isChecked()
        opts.subset_fonts = self.subset.isChecked()
        opts.remove_metadata = self.strip_meta.isChecked()
        opts.remove_annotations = self.strip_annots.isChecked()
        opts.remove_embedded_files = self.strip_forms.isChecked()
        opts.linearize = self.linearize.isChecked()
        opts.backend = self.backend.currentData()
        return opts

    def start(self) -> None:
        config.set("compress_profile", self.profile.value())
        opts = self._options()
        target = self.output.resolved(self.source, "compressed")
        self.run_job(comp.compress, self.source, target, opts, self.password,
                     name=f"Compressing {self.source.name}")

    def on_success(self, result: comp.CompressResult) -> None:
        self._set_metric(self.orig_label, utils.human_size(result.original_size))
        self._set_metric(self.est_label, utils.human_size(result.compressed_size))
        self._set_metric(self.save_label,
                         f"{result.saved_percent:.1f}%" if result.achieved else "0%")

        detail = [f"Engine: {result.backend}"]
        if result.images_recompressed:
            detail.append(f"{result.images_recompressed} of {result.images_processed} "
                          f"images re-encoded")
        if not result.achieved:
            detail.append("No reduction was possible without degrading the document.")

        self.show_result(result.message, [result.output] if result.output else [],
                         "\n".join(detail), result.warnings)
        self.record("Compress", self.source, result.output, result.pages,
                    f"{result.saved_percent:.1f}% saved")
        db.bump_stat("pdfs_processed")
        if result.achieved:
            db.bump_stat("bytes_saved", result.saved_bytes)
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast(result.message if result.achieved
                   else "Already optimised — no reduction possible",
                   "success" if result.achieved else "warning")
