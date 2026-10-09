"""
Stage the third-party binaries that get bundled into the installer.

Bundling Tesseract is what makes OCR work on a machine that has never had it
installed — which is the whole point of shipping a self-contained product.

    python tools/fetch_vendor.py --list         what is expected and where
    python tools/fetch_vendor.py --from <dir>   copy from an existing install
    python tools/fetch_vendor.py --check        verify what is staged

This script does NOT download anything by itself, deliberately: build machines
are often offline or behind a proxy, and silently pulling binaries off the
internet into a shipped product is a supply-chain risk. Point it at an install
you have already vetted instead.

LICENCES — read before shipping:
    Tesseract OCR   Apache 2.0   redistributable, include its LICENSE file
    Leptonica       BSD-2        redistributable
    Ghostscript     AGPL or commercial. Do NOT bundle the AGPL build in a
                    closed-source product; buy a commercial licence from
                    Artifex, or leave Ghostscript out (the app falls back to
                    its built-in compressor automatically).
    qpdf            Apache 2.0   redistributable
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

#: Binaries are only ever bundled for Windows, but keeping the names
#: platform-aware means the tool behaves sensibly when run anywhere else.
_EXE = ".exe" if os.name == "nt" else ""


def _bin(name: str) -> str:
    return f"{name}{_EXE}"

COMPONENTS = {
    "tesseract": {
        "target": VENDOR / "tesseract",
        "required": [_bin("tesseract")],
        "also": ["tessdata"],
        "source_hint": r"C:\Program Files\Tesseract-OCR",
        "download": "https://github.com/UB-Mannheim/tesseract/wiki",
        "why": "Offline OCR. Without it, the OCR screen reports that the engine "
               "is unavailable.",
        "licence": "Apache 2.0 — redistributable",
        "size": "~120 MB with the common language packs",
    },
    "gs": {
        "target": VENDOR / "gs",
        "required": [_bin("gswin64c") if os.name == "nt" else "gs"],
        "also": ["gsdll64.dll"] if os.name == "nt" else [],
        "source_hint": r"C:\Program Files\gs\gs10.04.0\bin",
        "download": "https://ghostscript.com/releases/gsdnld.html",
        "why": "Stronger compression on scanned documents. Optional — the "
               "built-in compressor is used when it is absent.",
        "licence": "AGPL or commercial — see the note at the top of this file",
        "size": "~40 MB",
    },
    "qpdf": {
        "target": VENDOR / "qpdf",
        "required": [_bin("qpdf")],
        "also": [],
        "source_hint": r"C:\Program Files\qpdf\bin",
        "download": "https://github.com/qpdf/qpdf/releases",
        "why": "One extra recovery strategy in Repair PDF. Optional.",
        "licence": "Apache 2.0 — redistributable",
        "size": "~15 MB",
    },
}

# Language packs worth shipping by default: the ones named in the product spec.
RECOMMENDED_LANGS = {
    "eng": "English",
    "hin": "Hindi",
    "ben": "Bengali",
    "spa": "Spanish",
    "fra": "French",
    "deu": "German",
    "ita": "Italian",
    "por": "Portuguese",
    "osd": "Orientation detection (needed for auto-rotate)",
}


def show_list() -> None:
    print(f"\nVendor folder: {VENDOR}\n")
    for name, spec in COMPONENTS.items():
        staged = spec["target"].is_dir() and any(
            (spec["target"] / f).exists() for f in spec["required"])
        mark = "STAGED  " if staged else "missing "
        print(f"[{mark}] {name}")
        print(f"           purpose  : {spec['why']}")
        print(f"           licence  : {spec['licence']}")
        print(f"           size     : {spec['size']}")
        print(f"           expects  : {spec['target']}\\{spec['required'][0]}")
        print(f"           typical  : {spec['source_hint']}")
        print(f"           download : {spec['download']}")
        print()

    print("Recommended Tesseract language packs (.traineddata in tessdata\\):")
    for code, label in RECOMMENDED_LANGS.items():
        present = (COMPONENTS["tesseract"]["target"] / "tessdata" /
                   f"{code}.traineddata").exists()
        print(f"   [{'x' if present else ' '}] {code:<8} {label}")
    print("\nTo stage a component:")
    print(r"   python tools\fetch_vendor.py --from ""C:\Program Files\Tesseract-OCR""")
    print()


def copy_from(source: Path) -> int:
    if not source.is_dir():
        print(f"ERROR: not a folder: {source}")
        return 1

    matched = None
    for name, spec in COMPONENTS.items():
        for required in spec["required"]:
            if (source / required).exists() or (source / "bin" / required).exists():
                matched = (name, spec)
                break
        if matched:
            break

    if matched is None:
        print(f"ERROR: could not tell what component lives in {source}.")
        print("       Expected one of: " + ", ".join(
            s["required"][0] for s in COMPONENTS.values()))
        return 1

    name, spec = matched
    target: Path = spec["target"]
    target.mkdir(parents=True, exist_ok=True)

    src_root = source if (source / spec["required"][0]).exists() else source / "bin"
    copied = 0

    for item in src_root.iterdir():
        if item.is_file() and item.suffix.lower() in (".exe", ".dll"):
            shutil.copy2(item, target / item.name)
            copied += 1

    # tessdata lives beside the binary, not in bin/
    for extra in spec["also"]:
        for candidate in (source / extra, src_root / extra, source.parent / extra):
            if candidate.is_dir():
                dest = target / extra
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(candidate, dest)
                copied += sum(1 for _ in dest.rglob("*") if _.is_file())
                break
            if candidate.is_file():
                shutil.copy2(candidate, target / extra)
                copied += 1
                break

    # keep the licence next to the binary -- required by Apache 2.0
    for licence_name in ("LICENSE", "LICENSE.txt", "COPYING", "LICENSE.md"):
        lic = source / licence_name
        if lic.is_file():
            shutil.copy2(lic, target / f"LICENSE-{name}.txt")
            break

    print(f"Staged {copied} file(s) into {target}")
    if name == "tesseract":
        tessdata = target / "tessdata"
        if tessdata.is_dir():
            langs = sorted(p.stem for p in tessdata.glob("*.traineddata"))
            print(f"Language packs found: {', '.join(langs) or 'none'}")
            missing = [c for c in RECOMMENDED_LANGS if c not in langs]
            if missing:
                print(f"Not present: {', '.join(missing)}")
                print("Add their .traineddata files to " + str(tessdata))
        else:
            print("WARNING: no tessdata folder was copied — OCR will not run.")
    return 0


def check() -> int:
    problems = []
    for name, spec in COMPONENTS.items():
        for required in spec["required"]:
            if not (spec["target"] / required).exists():
                problems.append(f"{name}: missing {required}")
    if not VENDOR.exists():
        print("No vendor folder — the build will not bundle any external tools.")
        print("OCR will then depend on Tesseract being installed separately on "
              "each user's machine.")
        return 1
    if problems:
        print("Staged, with gaps:")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("All components staged. The build will bundle them.")
    return 0


# ---------------------------------------------------------------------------
# automatic staging  (used by build.bat)
# ---------------------------------------------------------------------------

def _find_installed(component: str) -> Path | None:
    """Locate an installed copy of a component on this build machine."""
    import os
    import shutil

    if component == "tesseract":
        # reuse the application's own detection so build and runtime agree
        try:
            sys.path.insert(0, str(ROOT))
            from app.core import deps
            found = deps._tesseract_auto()          # noqa: SLF001 - same project
            if found:
                return Path(found).parent
        except Exception:
            pass
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR",
        ):
            if (candidate / "tesseract.exe").is_file():
                return candidate

    elif component == "gs":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "gs"
        if base.is_dir():
            for version in sorted(base.iterdir(), reverse=True):
                if (version / "bin" / "gswin64c.exe").is_file():
                    return version / "bin"

    elif component == "qpdf":
        found = shutil.which("qpdf")
        if found:
            return Path(found).parent

    return None


def auto_stage() -> int:
    """
    Stage whatever is already installed on this machine, quietly.

    build.bat calls this on every run so that a developer who installs Tesseract
    gets it bundled into the installer automatically — without that, the shipped
    application reports "OCR engine not found" on every customer's PC, which is
    exactly the trap this avoids.
    """
    if os.name != "nt":
        print("  (vendor bundling only applies to Windows builds — skipped)")
        return 0

    staged_any = False
    for name in ("tesseract", "gs", "qpdf"):
        spec = COMPONENTS[name]
        target: Path = spec["target"]

        already = any((target / f).exists() for f in spec["required"])
        if already:
            print(f"  {name:<11} already staged")
            staged_any = True
            continue

        source = _find_installed(name)
        if source is None:
            print(f"  {name:<11} not installed on this machine — skipped")
            continue

        # Ghostscript is AGPL; never bundle it without a deliberate decision.
        if name == "gs":
            print(f"  {name:<11} found at {source} but NOT staged automatically")
            print(f"  {'':<11} (Ghostscript is AGPL — see BUILDING.md section 4)")
            print(f"  {'':<11} stage it deliberately with:  --from \"{source}\"")
            continue

        print(f"  {name:<11} found at {source} — staging…")
        if copy_from(source) == 0:
            staged_any = True

    return 0 if staged_any else 0        # never fail the build over this


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                        help="show what is expected and what is staged")
    parser.add_argument("--from", dest="source", metavar="DIR",
                        help="copy a component from an existing installation")
    parser.add_argument("--check", action="store_true",
                        help="verify the staged components")
    parser.add_argument("--auto", action="store_true",
                        help="stage whatever is already installed on this machine")
    args = parser.parse_args()

    if args.source:
        return copy_from(Path(args.source))
    if args.check:
        return check()
    if args.auto:
        return auto_stage()
    show_list()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
