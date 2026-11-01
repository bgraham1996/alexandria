#!/usr/bin/env python3
"""Backfill PDFs onto link-only Zotero items.

Older imports (pyzotero 1.11.0) created Zotero items whose PDF upload silently
failed, leaving a "link only" item with no stored/annotatable PDF. The PDF was
still moved to the import output_dir (Sorted-Papers), renamed to its title.

This tool finds top-level items that have NO stored PDF but whose title matches
a PDF in that directory, and attaches the on-disk PDF to the existing item.

Only SAFE matches are attached: a unique link-only item mapped to a unique PDF
with a non-generic title. Ambiguous matches (the same PDF claimed by several
duplicate/mis-titled items, or a generic journal-name title) are listed and
SKIPPED for manual handling.

Dry-run by default; pass --apply to actually upload. Safe to re-run.
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from alexandria.config import load_env, load_settings, expand
from pyzotero import zotero

# Titles that are really a journal/container name — a sign title extraction
# failed, so we must not trust a filename match for them.
GENERIC_TITLE = re.compile(
    r'^(proceedings of the national academy|journal of|the journal of|'
    r'nature|science|cell|annual review of|frontiers in)\b', re.I)


def sanitise(title: str) -> str:
    """Mirror paper_importer's output-dir filename rule (so titles match files)."""
    clean = re.sub(r'[<>:"/\\|?*]', '', title or '')
    clean = clean.strip('. ')
    if len(clean) > 150:
        clean = clean[:150].rsplit(' ', 1)[0]
    return clean


def pdf_stored(zot, key: str) -> bool:
    """True if the item already has an imported_file PDF child with real content."""
    try:
        for c in zot.children(key):
            d = c.get("data", {})
            if (d.get("itemType") == "attachment"
                    and d.get("linkMode") == "imported_file"
                    and d.get("md5")):
                return True
    except Exception:
        pass
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually upload the PDFs (default: dry-run, writes nothing).")
    ap.add_argument("--source-dir",
                    help="Directory of PDFs to match against "
                         "(default: import.output_dir from config.yaml).")
    args = ap.parse_args(argv)

    load_env()
    s = load_settings()
    src = Path(expand(args.source_dir or s.get("import", {}).get("output_dir", "")))
    if not src.is_dir():
        print(f"Error: source dir not found: {src}")
        sys.exit(2)

    by_title: dict[str, Path] = {}
    for p in sorted(src.glob("*.pdf")):
        by_title.setdefault(p.stem.lower(), p)
        by_title.setdefault(re.sub(r" \(\d+\)$", "", p.stem).lower(), p)
    print(f"Indexed {len(list(src.glob('*.pdf')))} PDFs in {src}")

    zot = zotero.Zotero(os.environ["ZOTERO_LIBRARY_ID"],
                        s.get("zotero", {}).get("library_type", "user"),
                        os.environ["ZOTERO_API_KEY"])

    print("Fetching all top-level Zotero items...")
    items = zot.everything(zot.top())
    print(f"{len(items)} top-level items. Matching titles to on-disk PDFs...\n")

    # Collect candidate matches (link-only item <-> on-disk PDF).
    matches = []                       # (key, title, pdf)
    claims = defaultdict(list)         # pdf.name -> [keys]
    for it in items:
        d = it["data"]
        if d.get("itemType") in ("attachment", "note"):
            continue
        title = d.get("title", "")
        pdf = by_title.get(sanitise(title).lower())
        if not pdf or pdf_stored(zot, d["key"]):
            continue
        matches.append((d["key"], title, pdf))
        claims[pdf.name].append(d["key"])

    safe, ambiguous = [], []
    for key, title, pdf in matches:
        if len(claims[pdf.name]) > 1:
            ambiguous.append((key, title, pdf, "duplicate items claim same PDF"))
        elif GENERIC_TITLE.search(title or ""):
            ambiguous.append((key, title, pdf, "generic/journal-name title"))
        else:
            safe.append((key, title, pdf))

    if ambiguous:
        print(f"=== SKIPPED — {len(ambiguous)} ambiguous (handle manually) ===")
        for key, title, pdf, why in ambiguous:
            print(f"  [{key}] {title[:64]!r}  <- {why}")
        print()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== {mode} — {len(safe)} safe 1:1 match(es) ===")
    ok = fail = 0
    for key, title, pdf in safe:
        print(f"\n[{key}] {title[:72]}")
        print(f"    {'attach' if args.apply else 'would attach'}: {pdf.name}")
        if not args.apply:
            continue
        try:
            zot.attachment_simple([str(pdf)], key)
            if pdf_stored(zot, key):
                print("    -> attached & verified"); ok += 1
            else:
                print("    -> ERROR: upload returned but no stored file"); fail += 1
        except Exception as e:
            print(f"    -> FAILED: {e}"); fail += 1

    print(f"\n{'-'*60}")
    print(f"Safe matches: {len(safe)}   Ambiguous (skipped): {len(ambiguous)}")
    if args.apply:
        print(f"Attached & verified: {ok}   Failed: {fail}")
        sys.exit(1 if fail else 0)
    else:
        print("Dry run — nothing uploaded. Re-run with --apply to attach the safe set.")


if __name__ == "__main__":
    main()
