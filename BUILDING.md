# Building PDF Master Toolkit

This produces **`PDF_Master_Toolkit_Setup.exe`** — a normal Windows installer.
The person who runs it needs no Python, no pip, no command line and no
administrator rights.

---

## 1. What the build machine needs

You only need these on the machine where you *build*. They are never required
on the machines where the app is *installed*.

| Requirement | Version | Where |
|---|---|---|
| Windows | 10 or 11, 64-bit | — |
| Python | 3.11 – 3.14, 64-bit | https://www.python.org/downloads/ |
| Inno Setup | 6.x | https://jrsoftware.org/isdl.php |

> When installing Python, **tick "Add python.exe to PATH"** on the first screen.
>
> `build.bat` finds a suitable interpreter itself via the Windows `py` launcher,
> so having several Pythons installed side by side is fine.
>
> `requirements.txt` uses version *ranges*, not exact pins, on purpose: an exact
> pin breaks the moment a newer Python ships without a matching wheel. If a
> future Python release outpaces the packages again, installing the previous
> version alongside it is the fix — the script will find and prefer it.

Disk space: about 3 GB while building, ~450 MB for the finished installer.

---

## 2. Build it

Double-click **`build.bat`**, or run it from a command prompt:

```
build.bat
```

It does everything: creates a virtual environment, installs dependencies,
generates the brand assets and version resource, runs PyInstaller, then runs
Inno Setup.

When it finishes you get:

```
dist_installer\PDF_Master_Toolkit_Setup.exe     <- ship this
dist\PDF Master Toolkit\                        <- portable folder version
```

First run takes 10–15 minutes (mostly downloading packages). Later runs take
3–5 minutes.

---

## 3. Bundling the OCR engine — important

**OCR only works if the engine is present.** If Tesseract is neither bundled
into your build nor installed on the user's PC, the OCR screen says so and
every other tool carries on working.

`build.bat` now handles this for you: **install Tesseract once on the build
machine and it is bundled into the installer automatically** on every
subsequent build.

1. Install Tesseract on the build machine:
   https://github.com/UB-Mannheim/tesseract/wiki
   Download the **64-bit** installer. During setup, expand
   **Additional language data** and tick English, Hindi, Bengali, Spanish,
   French, German, Italian and Portuguese, plus **Orientation and script
   detection** (needed for auto-rotate).

2. Run `build.bat`. Step 5 finds the installation, copies it into `vendor\`
   and prints:

   ```
   Tesseract OCR   : BUNDLED  - OCR will work on every PC you install to
   ```

If it is *not* found, step 5 warns loudly and asks whether to continue, so you
cannot ship an OCR-less build by accident.

To check or stage manually:

```
.venv\Scripts\python.exe tools\fetch_vendor.py --list
.venv\Scripts\python.exe tools\fetch_vendor.py --auto
.venv\Scripts\python.exe tools\fetch_vendor.py --from "C:\Program Files\Tesseract-OCR"
```

This adds roughly 120 MB to the installer and is what makes the product
genuinely self-contained. The installer then exposes it as an optional
**component**, so end users can decline it if they want a smaller install.

### If a user still sees "OCR engine not found"

The OCR screen offers **Download Tesseract OCR**, **Check again** and
**Locate it myself…**. Installing Tesseract and pressing *Check again* enables
recognition immediately, with no restart. The app also looks in the registry
and in the chocolatey, scoop and winget install locations.

**Ghostscript** (optional, stronger compression on scans) and **qpdf**
(optional, one more repair strategy) stage the same way. Both are optional —
the app detects their absence and uses its own engines.

---

## 4. Licensing — read this before selling it

Two bundled components are **AGPL**, which requires you to release your source
code if you distribute them in a product:

| Component | Licence | What it does here |
|---|---|---|
| **PyMuPDF / MuPDF** | AGPL v3 or commercial | Core PDF engine — rendering, redaction, encryption |
| **Ghostscript** | AGPL v3 or commercial | Optional strong compression |

Your options:

1. **Buy commercial licences.** PyMuPDF is licensed by Artifex
   (https://artifex.com/licensing/). This is the normal route for a commercial
   product and the one to plan for.
2. **Release your source** under the AGPL.
3. **Build without them.** Leave Ghostscript unbundled (already the default)
   and swap PyMuPDF for `pypdf` + `pypdfium2`. That loses redaction, page
   rendering and the viewer, so it is a large change — see the note in
   `app/core/pdfbase.py`.

Everything else — Qt/PySide6 (LGPL, dynamically linked), Tesseract (Apache 2.0),
Pillow, openpyxl, python-docx, pdfplumber, ReportLab — is fine to ship in a
closed-source commercial product provided you keep their licence texts, which
the About screen already lists.

**Qt/LGPL note:** the build links PySide6 dynamically, which satisfies the LGPL.
Do not statically link Qt unless you have a commercial Qt licence.

---

## 5. Rebranding

Everything user-visible lives in **`app/branding.py`**. Edit it, rebuild, done.

```python
APP_NAME       = "PDF Master Toolkit"
COMPANY_NAME   = "SheetClub"
SUPPORT_EMAIL  = "support@sheetclub.com"
BRAND_ACCENT   = "#2F6BFF"      # changing this restyles the entire UI
```

The installer reads the same file (`tools/export_branding.py` regenerates
`installer/branding.iss` on every build), so product name, publisher, version,
URLs and the uninstall entry all follow automatically.

For the logo, drop real artwork into `assets/` using these names and rerun the
build:

| File | Used for | Suggested size |
|---|---|---|
| `icon.ico` | Executable and installer icon | multi-size, up to 256×256 |
| `logo_mark.png` | Sidebar and About tile | 256×256, transparent |
| `logo.png` | Wide lockup | 620×150, transparent |
| `splash.png` | Start-up splash | 520×300 |

`tools/make_assets.py` generates placeholders from the brand colours for any of
these that are missing, so the build never breaks on absent artwork.

---

## 6. Code signing (recommended)

Unsigned installers trigger Windows SmartScreen ("Windows protected your PC"),
which costs you a meaningful share of installs. With an EV or OV code-signing
certificate:

```
signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com ^
  /f "certificate.pfx" /p PASSWORD ^
  "dist\PDF Master Toolkit\PDF Master Toolkit.exe"

REM then rebuild the installer, and sign that too
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer\PDFMasterToolkit.iss

signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com ^
  /f "certificate.pfx" /p PASSWORD ^
  "dist_installer\PDF_Master_Toolkit_Setup.exe"
```

Sign the application **before** building the installer, then sign the installer
too — otherwise the warning simply moves.

---

## 7. Testing before release

Run through this on a **clean** Windows machine (or a fresh VM snapshot) that
has never had Python installed:

- [ ] Installer runs without administrator rights
- [ ] Application launches from the Start menu and the desktop shortcut
- [ ] Dashboard shows; drag a PDF onto it
- [ ] Merge two PDFs
- [ ] OCR a scanned PDF, then Ctrl+F for a word inside the result
- [ ] Compress a large PDF and check the reported saving is real
- [ ] Convert a PDF to Word and open it in Word
- [ ] Organise pages: reorder, rotate, delete, save
- [ ] Protect with a password, close, reopen and confirm it prompts
- [ ] Redact a phrase, then search the output to confirm it is gone
- [ ] Batch process 20 files
- [ ] Settings → Diagnostics shows the bundled Tesseract path
- [ ] Uninstall, and confirm it offers to keep or remove your data

---

## 8. Troubleshooting

**"no supported Python found"** — nothing in the 3.11–3.14 range is visible to
Windows. Run `py -0` to list what is installed, and install a 64-bit Python from
python.org if the list is empty or out of range.

**"No matching distribution found for <package>"** — that package has no build
for your Python version yet, which happens for a few months after each new
Python release. Install the previous Python version alongside yours; `build.bat`
prefers proven versions and will pick it up automatically.

**Dependency install fails** — usually a proxy blocking pypi.org. Try
`pip config set global.proxy http://your-proxy:port`.

**PyInstaller finishes but the app does not start** — set `DEBUG_CONSOLE = True`
near the top of `PDF_Master_Toolkit.spec`, rebuild, and run the exe from a
command prompt to see the error. Set it back to `False` before shipping.

**A tool screen shows "could not be loaded"** — a page module failed to import
in the frozen build. Its module name must appear in the `hiddenimports` list in
the spec file; PyInstaller cannot see the lazy `importlib` calls the registry
uses.

**Antivirus flags the installer** — common for unsigned PyInstaller output. Code
signing (section 6) is the real fix. UPX compression is already disabled in the
spec because it makes false positives markedly worse.

**Installer is very large** — expected: ~200 MB without OCR, ~350–450 MB with
the language packs. To trim, remove language packs you do not need from
`vendor\tesseract\tessdata\`, or comment out `pyhanko` in `requirements.txt` if
you do not need certificate signing.
