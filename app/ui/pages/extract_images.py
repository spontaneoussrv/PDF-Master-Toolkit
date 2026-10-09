"""Extract Images."""

from __future__ import annotations

from app import db
from app.core import images, utils
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class ExtractImagesPage(SingleFileToolPage):
    TITLE = "Extract Images"
    SUBTITLE = "Save the pictures embedded in a PDF"
    ACTION = "Extract images"
    OUTPUT_SUFFIX = "images"
    HISTORY_MODULE = "Extract Images"

    def build(self) -> None:
        self.add_input_section(
            "This pulls out the images stored inside the PDF. To turn whole pages "
            "into pictures instead, use PDF to Images.")

        card = C.Card("What to extract")
        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Pages", self.pages))

        self.fmt = C.combo([
            ("original", "Keep the original format (best quality)"),
            ("png", "Convert everything to PNG"),
            ("jpg", "Convert everything to JPEG"),
        ], "original")
        card.add(C.FormRow("Save as", self.fmt))

        self.min_size = C.spin(64, 1, 4000, " px")
        card.add(C.FormRow(
            "Minimum width and height", self.min_size,
            "Filters out icons, bullets and rules. Lower it if you expected more "
            "results."))

        self.min_kb = C.spin(2, 0, 10000, " KB")
        card.add(C.FormRow("Minimum file size", self.min_kb))

        self.skip_dupes = C.check(
            "Skip images that repeat on several pages", True,
            "A logo in a header is stored once and referenced many times.")
        card.add(self.skip_dupes)
        self.scroll.add(card)

        self.add_output_section(folder_mode=True)
        self.output.set_title("Output folder")
        self.add_run_section()

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        if self.source:
            self.output.dir_picker.setText(
                str(self.source.parent / f"{self.source.stem}_images"))

    def start(self) -> None:
        opts = images.ExtractImagesOptions(
            pages=self.pages.text(),
            fmt=self.fmt.currentData(),
            min_width=self.min_size.value(),
            min_height=self.min_size.value(),
            min_bytes=self.min_kb.value() * 1024,
            skip_duplicates=self.skip_dupes.isChecked(),
        )
        self.run_job(images.extract_images, self.source, self.output.output_dir(),
                     opts, self.password,
                     name=f"Extracting images from {self.source.name}")

    def on_success(self, result) -> None:
        if not result.outputs:
            self.show_result("No images matched the filters", [], result.message)
            self.set_status(result.message, "warning")
            return
        total = sum(utils.file_size(p) for p in result.outputs)
        self.show_result(
            f"Extracted {len(result.outputs)} image(s)",
            [result.folder] if result.folder else result.outputs,
            f"{utils.human_size(total)} saved in {result.folder}",
            result.warnings)
        self.record("Extract images", self.source, result.folder, len(result.outputs))
        db.bump_stat("pdfs_processed")
        if self.output.open_after.isChecked() and result.folder:
            utils.open_path(result.folder)
        self.toast(f"Extracted {len(result.outputs)} image(s)")
