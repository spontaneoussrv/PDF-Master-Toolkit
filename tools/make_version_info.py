"""
Write build_version.txt — the Windows VERSIONINFO resource PyInstaller stamps
into the executable, so the file's Properties dialog shows real product details
instead of "python.exe".

Run automatically by build.bat.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import branding                                        # noqa: E402

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver},
    prodvers={ver},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', {company!r}),
         StringStruct('FileDescription', {description!r}),
         StringStruct('FileVersion', {version!r}),
         StringStruct('InternalName', {internal!r}),
         StringStruct('LegalCopyright', {copyright!r}),
         StringStruct('OriginalFilename', {filename!r}),
         StringStruct('ProductName', {product!r}),
         StringStruct('ProductVersion', {version!r})])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version_tuple(version: str, build: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in version.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    try:
        b = int(build)
    except ValueError:
        b = 0
    return (parts[0], parts[1], parts[2], b)


def main() -> None:
    ver = version_tuple(branding.APP_VERSION, branding.APP_BUILD)
    content = TEMPLATE.format(
        ver=ver,
        company=branding.COMPANY_LEGAL_NAME or branding.COMPANY_NAME,
        description=branding.APP_TAGLINE,
        version=f"{branding.APP_VERSION}.{branding.APP_BUILD}",
        internal=branding.APP_SHORT_NAME,
        copyright=branding.COPYRIGHT,
        filename=f"{branding.APP_NAME}.exe",
        product=branding.APP_NAME,
    )
    target = ROOT / "build_version.txt"
    target.write_text(content, encoding="utf-8")
    print(f"Wrote {target}  ({branding.APP_VERSION}.{branding.APP_BUILD})")


if __name__ == "__main__":
    main()
