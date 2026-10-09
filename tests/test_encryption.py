"""
Regression tests for the PDF encryption paths.

The bug these exist to prevent: reading ``doc.needs_pass`` on an already
authenticated PyMuPDF document re-runs the password check and destroys the live
AES decryption state. Everything read or saved from that document afterwards
comes out EMPTY, and MuPDF only whispers "aes padding out of range" to stderr —
which the app suppresses. Unlock PDF silently produced blank files.

Run:  python tests/test_encryption.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf                                                   # noqa: E402

from app.core import metadata, pdf_ops, security                 # noqa: E402
from app.core.jobs import NullContext                            # noqa: E402
from app.core.pdfbase import open_pdf, was_encrypted             # noqa: E402

CANARY_A = "CANARY TEXT PAGE ONE"
CANARY_B = "CANARY TEXT PAGE TWO"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail and not condition else ""))


def make_source(folder: Path, name: str = "source.pdf") -> Path:
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((60, 120), CANARY_A, fontsize=18)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((60, 120), CANARY_B, fontsize=18)
    out = folder / name
    doc.save(out)
    doc.close()
    return out


def text_of(path: Path, index: int = 0) -> str:
    doc = pymupdf.open(str(path))
    try:
        return doc[index].get_text().strip()
    finally:
        doc.close()


def run() -> int:
    ctx = NullContext()
    with tempfile.TemporaryDirectory(prefix="pmt_enc_") as tmp:
        folder = Path(tmp)
        src = make_source(folder)

        print("\nencrypt -> decrypt round trip, every method")
        for method in ("aes256", "aes128", "rc4_128"):
            locked = security.protect(
                ctx, src, folder / f"locked_{method}.pdf",
                security.ProtectOptions(user_password="Pw12345!",
                                        owner_password="own", method=method),
            ).output
            check(f"{method}: encrypted file requires a password",
                  security.inspect_security(locked).needs_password)

            res = security.unlock(ctx, locked, "Pw12345!",
                                  folder / f"unlocked_{method}.pdf")
            out = res.output
            check(f"{method}: unlocked file opens without a password",
                  not security.inspect_security(out).needs_password)
            # THE regression: content must survive
            check(f"{method}: page 1 content preserved",
                  CANARY_A in text_of(out, 0), repr(text_of(out, 0)[:40]))
            check(f"{method}: page 2 content preserved",
                  CANARY_B in text_of(out, 1), repr(text_of(out, 1)[:40]))
            check(f"{method}: page count preserved", res.pages == 2, str(res.pages))
            check(f"{method}: reports it was encrypted",
                  "Protection removed" in res.message, res.message)

        print("\nowner-restrictions-only document")
        restricted = security.protect(
            ctx, src, folder / "restricted.pdf",
            security.ProtectOptions(
                user_password="", owner_password="own",
                allow={"print": True, "print_hq": True, "copy": False,
                       "modify": False, "annotate": False, "form": True,
                       "assemble": False, "accessibility": True}),
        ).output
        info = security.inspect_security(restricted)
        check("opens without a password", not info.needs_password)
        check("copying is restricted", "Copying text and images" in info.restricted,
              str(info.restricted))
        out = security.unlock(ctx, restricted, "", folder / "unrestricted.pdf").output
        check("restrictions removed, content intact", CANARY_A in text_of(out))

        print("\nwrong password is rejected")
        locked = folder / "locked_aes256.pdf"
        try:
            security.unlock(ctx, locked, "definitely-wrong", folder / "nope.pdf")
            check("wrong password raises", False, "no exception raised")
        except Exception as e:
            check("wrong password raises a readable error",
                  "not accepted" in str(e).lower(), str(e)[:60])
        check("no output written for a wrong password",
              not (folder / "nope.pdf").exists())

        print("\nno post-authentication needs_pass reads leak into other tools")
        doc = open_pdf(locked, "Pw12345!")
        try:
            check("open_pdf records the pre-auth encryption state", was_encrypted(doc))
            check("authenticated document still yields text",
                  CANARY_A in doc[0].get_text())
        finally:
            doc.close()

        got = pdf_ops.inspect(locked, "Pw12345!")
        check("inspect() reads an encrypted file with the password",
              got.pages == 2 and got.encrypted, f"pages={got.pages}")

        meta = metadata.read_metadata(locked, "Pw12345!")
        check("read_metadata() works on an encrypted file",
              meta.pages == 2 and meta.encrypted, f"pages={meta.pages}")

        print("\nprotect preserves content too")
        again = pymupdf.open(str(locked))
        again.authenticate("Pw12345!")
        check("encrypted file itself still holds its text",
              CANARY_A in again[0].get_text())
        again.close()

        print("\nrepeatability (10 consecutive round trips)")
        losses = 0
        for i in range(10):
            s = make_source(folder, f"r{i}.pdf")
            lk = security.protect(
                ctx, s, folder / f"rl{i}.pdf",
                security.ProtectOptions(user_password=f"pw{i}!",
                                        owner_password="o")).output
            uo = security.unlock(ctx, lk, f"pw{i}!", folder / f"ru{i}.pdf").output
            if CANARY_A not in text_of(uo):
                losses += 1
        check("10/10 round trips preserved content", losses == 0,
              f"{losses} lost content")

    print(f"\n{'='*58}")
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for f in FAILED:
            print(f"    FAILED: {f}")
    print(f"{'='*58}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(run())
