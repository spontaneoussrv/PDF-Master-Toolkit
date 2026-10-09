"""
Generate installer/branding.iss from app/branding.py.

This keeps a single source of truth: edit branding.py, and the installer's
product name, publisher, version, URLs and AppId all follow. Run automatically
by build.bat.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import branding                                        # noqa: E402


def esc(value: str) -> str:
    """Inno Setup escapes a literal brace by doubling it."""
    return str(value or "").replace("{", "{{")


def main() -> None:
    target = ROOT / "installer" / "branding.iss"
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "; ===========================================================",
        ";  GENERATED FILE - do not edit.",
        ";  Produced by tools/export_branding.py from app/branding.py.",
        "; ===========================================================",
        "",
        f'#define AppName          "{esc(branding.APP_NAME)}"',
        f'#define AppShortName     "{esc(branding.APP_SHORT_NAME)}"',
        f'#define AppVersion       "{esc(branding.APP_VERSION)}"',
        f'#define AppBuild         "{esc(branding.APP_BUILD)}"',
        f'#define AppId            "{esc(branding.APP_ID)}"',
        f'#define Publisher        "{esc(branding.COMPANY_LEGAL_NAME or branding.COMPANY_NAME)}"',
        f'#define PublisherShort   "{esc(branding.COMPANY_NAME)}"',
        f'#define AppURL           "{esc(branding.COMPANY_WEBSITE)}"',
        f'#define SupportURL       "{esc(branding.SUPPORT_URL or branding.COMPANY_WEBSITE)}"',
        f'#define UpdatesURL       "{esc(branding.UPDATES_URL or branding.COMPANY_WEBSITE)}"',
        f'#define SupportEmail     "{esc(branding.SUPPORT_EMAIL)}"',
        f'#define Copyright        "{esc(branding.COPYRIGHT)}"',
        f'#define IconFile         "{esc(branding.ICON_FILE)}"',
        f'#define ExeName          "{esc(branding.APP_NAME)}.exe"',
        f'#define OutputName       "PDF_Master_Toolkit_Setup"',
        "",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {target}")
    print(f"  product   : {branding.APP_NAME} {branding.APP_VERSION}")
    print(f"  publisher : {branding.COMPANY_NAME}")
    print(f"  installer : PDF_Master_Toolkit_Setup.exe")


if __name__ == "__main__":
    main()
