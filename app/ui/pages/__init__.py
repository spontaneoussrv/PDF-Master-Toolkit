"""
Module registry.

Every destination in the sidebar is declared once here. The sidebar, the
command palette, the dashboard quick actions and the router all read this list,
so adding a new tool means adding one :class:`ModuleSpec` and one page class —
nothing else in the app needs to change.

Page classes are imported lazily on first navigation, which keeps startup fast
no matter how many modules exist.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Type


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    title: str
    section: str
    module: str                 # dotted path under app.ui.pages
    cls: str                    # class name inside that module
    icon: str = "•"
    subtitle: str = ""
    keywords: tuple[str, ...] = ()
    hidden: bool = False        # reachable, but not listed in the sidebar

    def load(self) -> Type:
        mod = importlib.import_module(f"app.ui.pages.{self.module}")
        return getattr(mod, self.cls)


SECTIONS = ["HOME", "ORGANISE", "RECOGNISE", "CONVERT", "EDIT",
            "SECURE", "OPTIMISE", "CAPTURE", "AUTOMATE", "SYSTEM"]


MODULES: list[ModuleSpec] = [
    # ---------------------------------------------------------------- HOME
    ModuleSpec("dashboard", "Dashboard", "HOME", "dashboard", "DashboardPage",
               "◈", "Your PDF workspace at a glance",
               ("home", "start", "overview")),
    ModuleSpec("viewer", "PDF Viewer", "HOME", "viewer_page", "ViewerPage",
               "◫", "Read, search and copy from any PDF",
               ("read", "open", "preview", "search", "zoom")),

    # ------------------------------------------------------------ ORGANISE
    ModuleSpec("merge", "Merge PDF", "ORGANISE", "merge", "MergePage",
               "⧉", "Combine several PDFs into one",
               ("combine", "join", "append")),
    ModuleSpec("split", "Split PDF", "ORGANISE", "split", "SplitPage",
               "⑃", "Break a PDF into smaller files",
               ("divide", "separate", "chunks")),
    ModuleSpec("extract_pages", "Extract Pages", "ORGANISE", "extract_pages",
               "ExtractPagesPage", "⬒", "Pull selected pages into a new PDF",
               ("pick", "select", "subset")),
    ModuleSpec("organize", "Organise Pages", "ORGANISE", "organize", "OrganizePage",
               "▦", "Reorder, rotate, delete and insert pages visually",
               ("arrange", "reorder", "sort", "delete", "duplicate")),
    ModuleSpec("rotate", "Rotate Pages", "ORGANISE", "rotate", "RotatePage",
               "↻", "Turn pages 90, 180 or 270 degrees",
               ("turn", "orientation", "landscape")),
    ModuleSpec("crop", "Crop PDF", "ORGANISE", "crop", "CropPage",
               "⛶", "Trim margins visually or by measurement",
               ("trim", "margins", "cut")),
    ModuleSpec("pagesize", "Resize Pages", "ORGANISE", "pagesize", "PageSizePage",
               "▭", "Scale every page to A4, Letter or another size",
               ("a4", "letter", "normalise", "normalize", "scale", "uniform")),

    # ----------------------------------------------------------- RECOGNISE
    ModuleSpec("ocr", "OCR PDF", "RECOGNISE", "ocr_page", "OcrPage",
               "◍", "Make scanned documents searchable",
               ("scan", "recognise", "recognize", "searchable", "text layer",
                "tesseract", "hindi", "bengali")),
    ModuleSpec("extract_text", "Extract Text", "RECOGNISE", "extract_text",
               "ExtractTextPage", "T", "Pull the text out of a PDF",
               ("copy", "txt", "docx", "json")),
    ModuleSpec("extract_images", "Extract Images", "RECOGNISE", "extract_images",
               "ExtractImagesPage", "⧉", "Save the pictures embedded in a PDF",
               ("photos", "pictures", "embedded")),

    # ------------------------------------------------------------- CONVERT
    ModuleSpec("pdf2word", "PDF to Word", "CONVERT", "convert_pages", "PdfToWordPage",
               "W", "Create an editable .docx", ("docx", "office", "editable")),
    ModuleSpec("word2pdf", "Word to PDF", "CONVERT", "convert_pages", "WordToPdfPage",
               "◧", "Convert .docx documents to PDF", ("docx", "office")),
    ModuleSpec("pdf2excel", "PDF to Excel", "CONVERT", "convert_pages", "PdfToExcelPage",
               "X", "Extract tables into a workbook",
               ("xlsx", "csv", "tables", "spreadsheet")),
    ModuleSpec("excel2pdf", "Excel to PDF", "CONVERT", "convert_pages", "ExcelToPdfPage",
               "◨", "Convert spreadsheets to PDF", ("xlsx", "csv", "spreadsheet")),
    ModuleSpec("pdf2img", "PDF to Images", "CONVERT", "convert_pages", "PdfToImagesPage",
               "🖼", "Render pages as PNG, JPG, WEBP or TIFF",
               ("png", "jpg", "render", "raster", "dpi")),
    ModuleSpec("img2pdf", "Images to PDF", "CONVERT", "convert_pages", "ImagesToPdfPage",
               "▤", "Build a PDF from photos or scans",
               ("png", "jpg", "photos", "combine")),

    # ---------------------------------------------------------------- EDIT
    ModuleSpec("watermark", "Watermark", "EDIT", "watermark", "WatermarkPage",
               "◈", "Stamp text or a logo across the pages",
               ("confidential", "draft", "logo", "stamp")),
    ModuleSpec("headerfooter", "Header & Footer", "EDIT", "headerfooter",
               "HeaderFooterPage", "▤", "Add running headers and footers",
               ("running head", "date", "filename")),
    ModuleSpec("pagenumbers", "Page Numbers", "EDIT", "pagenumbers", "PageNumbersPage",
               "№", "Number the pages in any style",
               ("numbering", "roman", "folio")),
    ModuleSpec("sign", "Sign PDF", "EDIT", "sign", "SignPage",
               "✎", "Place a signature, or sign with a certificate",
               ("signature", "esign", "certificate", "pfx")),
    ModuleSpec("redact", "Redact PDF", "EDIT", "redact", "RedactPage",
               "▪", "Permanently remove sensitive content",
               ("black out", "remove", "censor", "gdpr")),

    # -------------------------------------------------------------- SECURE
    ModuleSpec("protect", "Protect PDF", "SECURE", "protect", "ProtectPage",
               "🔒", "Encrypt with a password and set permissions",
               ("password", "encrypt", "aes", "permissions")),
    ModuleSpec("unlock", "Unlock PDF", "SECURE", "unlock", "UnlockPage",
               "🔓", "Remove protection using the correct password",
               ("decrypt", "password", "remove protection")),
    ModuleSpec("metadata", "PDF Metadata", "SECURE", "metadata_page", "MetadataPage",
               "ⓘ", "View, edit or strip document properties",
               ("properties", "author", "title", "privacy")),

    # ------------------------------------------------------------- OPTIMISE
    ModuleSpec("compress", "Compress PDF", "OPTIMISE", "compress_page", "CompressPage",
               "⤓", "Reduce file size for email and storage",
               ("shrink", "smaller", "optimise", "optimize", "size")),
    ModuleSpec("repair", "Repair PDF", "OPTIMISE", "repair_page", "RepairPage",
               "⚕", "Recover a damaged or unreadable file",
               ("fix", "corrupt", "damaged", "recover")),
    ModuleSpec("compare", "Compare PDFs", "OPTIMISE", "compare_page", "ComparePage",
               "⇄", "Find what changed between two documents",
               ("diff", "difference", "versions")),

    # -------------------------------------------------------------- CAPTURE
    ModuleSpec("scan", "Scan to PDF", "CAPTURE", "scan_page", "ScanPage",
               "⎙", "Scan paper directly into a searchable PDF",
               ("scanner", "wia", "twain", "paper")),

    # ------------------------------------------------------------- AUTOMATE
    ModuleSpec("batch", "Batch Processing", "AUTOMATE", "batch_page", "BatchPage",
               "≡", "Run one job across hundreds of files",
               ("bulk", "many", "queue", "mass")),
    ModuleSpec("workflow", "Workflow Builder", "AUTOMATE", "workflow_page",
               "WorkflowPage", "⛓", "Chain several tools into one saved recipe",
               ("chain", "pipeline", "automation", "recipe")),
    ModuleSpec("visits", "Visit Sorter", "AUTOMATE", "visits_page", "VisitsPage",
               "🗓", "Detect visit sets, sort by date and split by size",
               ("care", "rn", "aide", "date order", "excel", "medical")),

    # --------------------------------------------------------------- SYSTEM
    ModuleSpec("history", "History", "SYSTEM", "history_page", "HistoryPage",
               "🕘", "Everything you have processed",
               ("recent", "log", "activity")),
    ModuleSpec("settings", "Settings", "SYSTEM", "settings_page", "SettingsPage",
               "⚙", "Preferences, OCR paths and diagnostics",
               ("preferences", "options", "config", "tesseract")),
    ModuleSpec("about", "About", "SYSTEM", "about_page", "AboutPage",
               "ⓘ", "Version, support and privacy",
               ("version", "help", "support", "licence", "license", "privacy")),
]


BY_KEY: dict[str, ModuleSpec] = {m.key: m for m in MODULES}


def get(key: str) -> ModuleSpec | None:
    return BY_KEY.get(key)


def visible_modules() -> list[ModuleSpec]:
    return [m for m in MODULES if not m.hidden]


def by_section() -> dict[str, list[ModuleSpec]]:
    out: dict[str, list[ModuleSpec]] = {}
    for m in visible_modules():
        out.setdefault(m.section, []).append(m)
    return {s: out[s] for s in SECTIONS if s in out}


def search(query: str) -> list[ModuleSpec]:
    """Fuzzy-ish search over title, subtitle and keywords."""
    q = (query or "").strip().lower()
    if not q:
        return visible_modules()
    scored: list[tuple[int, ModuleSpec]] = []
    for m in MODULES:
        title = m.title.lower()
        score = 0
        if title.startswith(q):
            score = 100
        elif q in title:
            score = 80
        elif any(k.startswith(q) for k in m.keywords):
            score = 60
        elif any(q in k for k in m.keywords):
            score = 45
        elif q in m.subtitle.lower():
            score = 30
        elif all(ch in title for ch in q):
            score = 10
        if score:
            scored.append((score, m))
    scored.sort(key=lambda t: (-t[0], t[1].title))
    return [m for _s, m in scored]


#: shown as big buttons on the dashboard
QUICK_ACTIONS = ["merge", "ocr", "compress", "pdf2word", "img2pdf", "organize"]
