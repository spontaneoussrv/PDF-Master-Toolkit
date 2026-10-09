"""
Scan to PDF via Windows Image Acquisition (WIA).

Scanner support is the most environment-dependent feature in the product, so
it is fully isolated here: every entry point returns a clear "unavailable"
state instead of raising, nothing is imported at module load that only exists
on Windows, and a failure in this module can never destabilise the rest of the
application.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app import paths
from app.core import deps, utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import pymupdf

# WIA format GUIDs
WIA_FORMAT = {
    "bmp": "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}",
    "png": "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}",
    "jpeg": "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}",
    "tiff": "{B96B3CB1-0728-11D3-9D7B-0000F81EF32E}",
}

# WIA device property ids
WIA_PROP = {
    "horizontal_resolution": 6147,
    "vertical_resolution": 6148,
    "horizontal_extent": 6151,
    "vertical_extent": 6152,
    "brightness": 6154,
    "contrast": 6155,
    "data_type": 4103,
    "document_handling_select": 3088,
}

DATA_TYPE = {"color": 3, "grayscale": 2, "blackwhite": 0}
FEEDER = 1
FLATBED = 2

UNAVAILABLE_MESSAGE = (
    "No scanner interface is available on this computer.\n\n"
    "Scan to PDF uses Windows Image Acquisition (WIA). It needs Windows, a "
    "WIA-compatible scanner driver, and the scanner switched on and connected.\n\n"
    "You can still build a PDF from images your scanner software saves, using "
    "Images to PDF."
)


@dataclass
class ScannerDevice:
    device_id: str
    name: str
    manufacturer: str = ""

    def __str__(self) -> str:
        return self.name or self.device_id


@dataclass
class ScanOptions:
    dpi: int = 300
    color_mode: str = "color"             # color | grayscale | blackwhite
    source: str = "flatbed"               # flatbed | feeder
    duplex: bool = False
    brightness: int = 0                   # -1000..1000
    contrast: int = 0
    page_size: str = "A4"
    fmt: str = "png"
    max_pages: int = 50


@dataclass
class ScannedPage:
    index: int
    path: Path
    rotation: int = 0
    deleted: bool = False
    width: int = 0
    height: int = 0


@dataclass
class ScanResult:
    pages: list[ScannedPage] = field(default_factory=list)
    device: str = ""
    message: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------

def is_available() -> bool:
    return deps.has_wia()


def unavailable_reason() -> str:
    import os
    if os.name != "nt":
        return "Scanning requires Windows."
    if not deps.has_pywin32():
        return ("The Windows scanner components are not present in this build "
                "of the application.")
    return ("No WIA scanner was found. Check that the scanner is switched on, "
            "connected, and that its Windows driver is installed.")


def list_devices() -> list[ScannerDevice]:
    """Enumerate connected WIA scanners. Returns [] when none are available."""
    if not is_available():
        return []
    try:
        import pythoncom                                   # type: ignore
        import win32com.client                             # type: ignore
    except ImportError:
        return []

    devices: list[ScannerDevice] = []
    try:
        pythoncom.CoInitialize()
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        for info in manager.DeviceInfos:
            try:
                if int(info.Type) != 1:                    # 1 == scanner
                    continue
            except Exception:
                pass
            name, manufacturer = "", ""
            try:
                for prop in info.Properties:
                    if prop.Name == "Name":
                        name = str(prop.Value)
                    elif prop.Name == "Manufacturer":
                        manufacturer = str(prop.Value)
            except Exception:
                pass
            devices.append(ScannerDevice(str(info.DeviceID), name or "Scanner", manufacturer))
    except Exception:
        return []
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    return devices


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------

def scan(
    ctx: JobContext,
    device_id: str | None = None,
    opts: ScanOptions | None = None,
    out_dir: str | Path | None = None,
) -> ScanResult:
    """Acquire pages from a scanner. Raises JobError with actionable text."""
    if not is_available():
        raise JobError(UNAVAILABLE_MESSAGE)

    opts = opts or ScanOptions()
    folder = Path(out_dir) if out_dir else Path(
        tempfile.mkdtemp(prefix="pmt_scan_", dir=str(paths.temp_dir()))
    )
    folder.mkdir(parents=True, exist_ok=True)
    result = ScanResult()

    import pythoncom                                       # type: ignore
    import win32com.client                                 # type: ignore

    pythoncom.CoInitialize()
    try:
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        target = None
        for info in manager.DeviceInfos:
            if device_id is None or str(info.DeviceID) == device_id:
                target = info
                break
        if target is None:
            raise JobError("The selected scanner is no longer connected.")

        device = target.Connect()
        result.device = str(getattr(target, "DeviceID", ""))

        try:
            _apply_device_settings(device, opts)
        except Exception as e:
            result.warnings.append(f"Some scanner settings were not accepted: {e}")

        item = device.Items[1]
        _apply_item_settings(item, opts)

        ctx.stage("Scanning")
        page_no = 0
        fmt = WIA_FORMAT.get(opts.fmt, WIA_FORMAT["png"])

        while page_no < opts.max_pages:
            ctx.check_cancel()
            page_no += 1
            ctx.progress(min(95, page_no * 3), f"Scanning page {page_no}…")
            try:
                image = item.Transfer(fmt)
            except Exception as e:
                msg = str(e)
                if page_no == 1:
                    raise JobError(f"The scanner reported an error: {msg}") from e
                # Feeder empty is the normal end-of-batch signal.
                break

            path = folder / f"scan_{page_no:04d}.{opts.fmt}"
            try:
                image.SaveFile(str(path))
            except Exception as e:
                result.warnings.append(f"Page {page_no} could not be saved: {e}")
                break

            sp = ScannedPage(index=page_no, path=path)
            try:
                sp.width = int(image.Width)
                sp.height = int(image.Height)
            except Exception:
                pass
            result.pages.append(sp)
            ctx.log(f"Scanned page {page_no}")

            if opts.source != "feeder":
                break

        if not result.pages:
            raise JobError("No pages were scanned. Check that a document is loaded.")

    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    result.message = f"Scanned {len(result.pages)} page(s)"
    ctx.progress(100, result.message)
    return result


def _apply_device_settings(device, opts: ScanOptions) -> None:
    handling = FEEDER if opts.source == "feeder" else FLATBED
    if opts.duplex and opts.source == "feeder":
        handling |= 0x004                                  # DUPLEX
    _set_prop(device.Properties, WIA_PROP["document_handling_select"], handling)


def _apply_item_settings(item, opts: ScanOptions) -> None:
    props = item.Properties
    dpi = int(utils.clamp(opts.dpi, 75, 1200))
    _set_prop(props, WIA_PROP["horizontal_resolution"], dpi)
    _set_prop(props, WIA_PROP["vertical_resolution"], dpi)
    _set_prop(props, WIA_PROP["data_type"], DATA_TYPE.get(opts.color_mode, 3))
    _set_prop(props, WIA_PROP["brightness"], int(utils.clamp(opts.brightness, -1000, 1000)))
    _set_prop(props, WIA_PROP["contrast"], int(utils.clamp(opts.contrast, -1000, 1000)))

    from app.core.pdfbase import PAGE_SIZES
    ps = PAGE_SIZES.get(opts.page_size)
    if ps:
        _set_prop(props, WIA_PROP["horizontal_extent"], int(ps.width / 72.0 * dpi))
        _set_prop(props, WIA_PROP["vertical_extent"], int(ps.height / 72.0 * dpi))


def _set_prop(properties, prop_id: int, value) -> None:
    try:
        for p in properties:
            if int(p.PropertyID) == prop_id:
                p.Value = value
                return
    except Exception:
        pass


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

def pages_to_pdf(
    ctx: JobContext,
    pages: Sequence[ScannedPage],
    output: str | Path,
    page_size: str = "A4",
    fit: str = "fit",
    quality: int = 88,
) -> Path:
    """Turn scanned page images into a PDF (OCR is applied separately)."""
    from app.core.images import ImageItem, ImagesToPdfOptions, images_to_pdf

    live = [p for p in pages if not p.deleted and p.path.exists()]
    if not live:
        raise JobError("There are no scanned pages to save.")

    items = [ImageItem(p.path, rotation=p.rotation) for p in live]
    opts = ImagesToPdfOptions(page_size=page_size, fit=fit, quality=quality,
                              auto_orient=True, margin=0.0)
    res = images_to_pdf(ctx, items, output, opts)
    if not res.outputs:
        raise JobError("The PDF could not be created from the scanned pages.")
    return res.outputs[0]
