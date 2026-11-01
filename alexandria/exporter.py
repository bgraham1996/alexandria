#!/usr/bin/env python3
"""
Zotero → Obsidian Note Creator
===============================
Scans your Zotero library for items tagged with a configurable tag (default:
'obs') and creates an Obsidian-compatible markdown note for each one.

Each note includes:
  - Filename = paper title
  - YAML frontmatter with authors and publication date
  - Abstract in the body

The script tracks which items have already been exported (via a small JSON
state file) so it can be run repeatedly without duplicating notes.

Setup:
    Uses the same .env file as paper_importer.py:
        ZOTERO_LIBRARY_ID=your_user_id
        ZOTERO_API_KEY=your_api_key

    Additionally set (or pass via CLI):
        OBSIDIAN_VAULT_PATH=~/path/to/your/vault
        OBSIDIAN_NOTES_FOLDER=Papers   (subfolder within the vault)

Usage:
    python zotero_to_obsidian.py
    python zotero_to_obsidian.py --vault ~/Documents/MyVault
    python zotero_to_obsidian.py --folder "Reading Notes"
    python zotero_to_obsidian.py --tag "to-obsidian"
    python zotero_to_obsidian.py --dry-run
    python zotero_to_obsidian.py --remove-tag   (removes 'obs' tag after export)

Exit codes:
    0 = success (notes created, or nothing new to export)
    1 = error (API failure, write failure, etc.)
    2 = configuration error (missing API key, vault path, etc.)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from alexandria.config import load_env, load_settings, expand, STATE_FILE

try:
    from pyzotero import zotero
except ImportError:
    print("Error: pyzotero not installed. Run: pip install pyzotero")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load credentials from configs/.env and settings from config.yaml."""
    load_env()
    settings = load_settings()
    obs = settings.get("obsidian", {})

    config = {
        "library_id": os.environ.get("ZOTERO_LIBRARY_ID", ""),
        "library_type": settings.get("zotero", {}).get("library_type", "user"),
        "api_key": os.environ.get("ZOTERO_API_KEY", ""),
        "vault_path": expand(obs.get("vault_path", "")),
        "notes_folder": obs.get("notes_folder", "Papers"),
    }

    if not config["library_id"] or not config["api_key"]:
        print("Error: ZOTERO_LIBRARY_ID and ZOTERO_API_KEY must be set.")
        print("Get these from: https://www.zotero.org/settings/keys")
        print()
        print("Either set environment variables or create a .env file alongside")
        print("this script with:")
        print("  ZOTERO_LIBRARY_ID=your_user_id")
        print("  ZOTERO_API_KEY=your_api_key")
        sys.exit(2)

    return config


# ---------------------------------------------------------------------------
# State tracking — avoid re-exporting items
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load the set of already-exported Zotero item keys."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"exported_keys": []}
    return {"exported_keys": []}


def save_state(state: dict):
    """Persist the export state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Zotero data extraction helpers
# ---------------------------------------------------------------------------

def extract_authors(item_data: dict) -> list[str]:
    """Extract author names from a Zotero item's 'creators' field."""
    authors = []
    for creator in item_data.get("creators", []):
        if creator.get("creatorType") in ("author", "editor", "contributor"):
            first = creator.get("firstName", "")
            last = creator.get("lastName", "")
            name = creator.get("name", "")  # single-field name
            if name:
                authors.append(name)
            elif first or last:
                authors.append(f"{first} {last}".strip())
    return authors


def extract_date(item_data: dict) -> str:
    """Extract and normalise the publication date.

    Zotero stores dates in various formats: '2024-03-15', '2024-03',
    'March 2024', '2024', etc. We return whatever is there, cleaned up.
    """
    raw = item_data.get("date", "")
    if not raw:
        return ""
    # If it's already ISO-ish, return as-is
    if re.match(r"^\d{4}(-\d{2})?(-\d{2})?$", raw):
        return raw
    # Try parsing common formats
    for fmt in ("%B %d, %Y", "%B %Y", "%b %Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Fall back to the raw string
    return raw


def extract_abstract(item_data: dict) -> str:
    """Extract the abstract, cleaning up common formatting issues."""
    abstract = item_data.get("abstractNote", "")
    if not abstract:
        return ""
    # Collapse single newlines (paragraph breaks) but keep double newlines
    abstract = re.sub(r"(?<!\n)\n(?!\n)", " ", abstract)
    return abstract.strip()


# ---------------------------------------------------------------------------
# Markdown note generation
# ---------------------------------------------------------------------------

def sanitise_filename(title: str) -> str:
    """Make a title safe for use as a filename on all platforms."""
    # Remove characters that are problematic in filenames
    clean = re.sub(r'[<>:"/\\|?*#^[\]]', '', title)
    # Collapse whitespace
    clean = re.sub(r'\s+', ' ', clean).strip('. ')
    # Truncate to reasonable length
    if len(clean) > 200:
        clean = clean[:200].rsplit(' ', 1)[0]
    return clean


def build_note(item_data: dict, vault_name: str = "", notes_folder: str = "Papers") -> str:
    """Build a markdown note with YAML frontmatter from a Zotero item."""
    title = item_data.get("title", "Untitled")
    authors = extract_authors(item_data)
    date = extract_date(item_data)
    abstract = extract_abstract(item_data)
    url = item_data.get("url", "")
    doi = item_data.get("DOI", "")
    zotero_key = item_data.get("key", "")
    safe_title = sanitise_filename(title)

    # --- YAML frontmatter ---
    lines = ["---"]
    # Title in frontmatter (useful for Obsidian dataview queries)
    lines.append(f'title: "{title}"')

    # Authors as a YAML list
    if authors:
        lines.append("authors:")
        for author in authors:
            lines.append(f'  - "{author}"')

    # Date
    if date:
        lines.append(f"date: {date}")

    # Optional: DOI and URL for reference
    if doi:
        lines.append(f"doi: {doi}")
    if url:
        lines.append(f"url: {url}")

    # Item type
    item_type = item_data.get("itemType", "")
    if item_type:
        lines.append(f"FileType: 📜 Paper")

    # Publication
    publication = item_data.get("publicationTitle", "")
    if publication:
        lines.append(f'publication: "{publication}"')

    lines.append("Reading Status: 🟥 Not Read")

    # Zotero item key (for cross-referencing)
    if zotero_key:
        lines.append(f"zotero_key: {zotero_key}")

    lines.append("---")
    lines.append("")

    # --- Body ---
    lines.append(f"# {title}")
    lines.append("")

    # Open in Zotero button (requires Buttons plugin)
    if zotero_key:
        zotero_uri = f"zotero://select/library/items/{zotero_key}"
        lines.append("```button")
        lines.append("name 📂 Open in Zotero")
        lines.append(f"type link")
        lines.append(f"action {zotero_uri}")
        lines.append("```")
        lines.append("")

    # Create Reading Notes button (requires Buttons + Advanced URI plugins)
    # Uses mode=new so Advanced URI appends (1), (2), etc. if file exists,
    # allowing multiple reading note files per paper.
    if vault_name:
        note_filepath = f"{notes_folder}/{safe_title} - Notes"
        note_data = (
            "---\n"
            "Status: \U0001f95c\n"
            "ReviewDate:\n"
            "Priority: \U0001f7e2\n"
            "FileType: ReadingNote \U0001f4d6\n"
            "Review Count: 0\n"
            "---\n\n"
            f'Paper: "[[{safe_title}]]"\n'
        )
        adv_uri = (
            f"obsidian://adv-uri"
            f"?vault={quote(vault_name)}"
            f"&filepath={quote(note_filepath)}"
            f"&data={quote(note_data)}"
            f"&mode=new"
        )
        lines.append("```button")
        lines.append("name 📝 New Reading Notes")
        lines.append("type link")
        lines.append(f"action {adv_uri}")
        lines.append("```")
        lines.append("")

    if abstract:
        lines.append("## Abstract")
        lines.append("")
        lines.append(abstract)
        lines.append("")

    # Reading Notes section with Dataview query
    lines.append("## Reading Notes")
    lines.append("")
    lines.append("```dataview")
    lines.append("TABLE file.name AS \"Note\", file.cday AS \"Created\", ReviewDate, Status")
    lines.append(f'FROM [[{safe_title}]]')
    lines.append('WHERE FileType = "ReadingNote 📖"')
    lines.append("SORT file.cday DESC")
    lines.append("```")
    lines.append("")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def export_items(
    tag: str = "obs",
    vault_path: str = "",
    notes_folder: str = "Papers",
    remove_tag: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
) -> list[dict]:
    """Fetch tagged Zotero items and create Obsidian notes."""
    config = load_config()

    # Override vault/folder from config if not passed explicitly
    if not vault_path:
        vault_path = config["vault_path"]
    if not vault_path:
        print("Error: No Obsidian vault path specified.")
        print("Set OBSIDIAN_VAULT_PATH in .env or pass --vault")
        sys.exit(2)

    vault = Path(vault_path).expanduser()
    if not vault.is_dir():
        print(f"Error: Vault path does not exist: {vault}")
        sys.exit(2)

    output_dir = vault / notes_folder
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    def log(msg):
        if verbose:
            print(msg)

    # Connect to Zotero
    log(f"Connecting to Zotero library {config['library_id']}...")
    zot = zotero.Zotero(
        config["library_id"],
        config["library_type"],
        config["api_key"],
    )

    # Fetch all items with the target tag
    log(f"Fetching items tagged '{tag}'...")
    try:
        items = zot.everything(zot.top(tag=tag))
    except Exception as e:
        print(f"Error fetching from Zotero API: {e}")
        sys.exit(1)

    log(f"Found {len(items)} item(s) tagged '{tag}'.")

    if not items:
        log("Nothing to export.")
        return []

    # Load state to skip already-exported items
    state = load_state()
    exported_keys = set(state.get("exported_keys", []))

    results = []
    new_exports = 0

    for item in items:
        data = item.get("data", {})
        key = data.get("key", "")
        title = data.get("title", "Untitled")

        # Skip attachments, notes, etc.
        if data.get("itemType") in ("attachment", "note", "annotation"):
            continue

        # Skip already exported
        if key in exported_keys:
            log(f"  Skipping (already exported): {title}")
            continue

        log(f"  Processing: {title}")

        # Build the note
        note_content = build_note(data, vault_name=vault.name, notes_folder=notes_folder)
        filename = sanitise_filename(title) + ".md"
        filepath = output_dir / filename

        result = {
            "key": key,
            "title": title,
            "filename": filename,
            "status": "pending",
        }

        if dry_run:
            log(f"    [DRY RUN] Would create: {filepath}")
            result["status"] = "dry_run"
        else:
            try:
                # Handle filename collisions
                if filepath.exists():
                    stem = filepath.stem
                    for i in range(2, 100):
                        filepath = output_dir / f"{stem} ({i}).md"
                        if not filepath.exists():
                            break
                    result["filename"] = filepath.name

                filepath.write_text(note_content, encoding="utf-8")
                log(f"    Created: {filepath.name}")
                result["status"] = "created"
                result["path"] = str(filepath)

                # Track as exported
                exported_keys.add(key)
                new_exports += 1

                # Optionally remove the tag from Zotero
                if remove_tag:
                    try:
                        tags = data.get("tags", [])
                        new_tags = [t for t in tags if t.get("tag") != tag]
                        data["tags"] = new_tags
                        zot.update_item(item)
                        log(f"    Removed '{tag}' tag from Zotero item")
                    except Exception as e:
                        log(f"    Warning: Could not remove tag: {e}")

            except Exception as e:
                log(f"    Error: {e}")
                result["status"] = "error"
                result["error"] = str(e)

        results.append(result)

    # Save updated state
    if not dry_run:
        state["exported_keys"] = sorted(exported_keys)
        state["last_run"] = datetime.now().isoformat()
        save_state(state)

    log(f"\nDone. {new_exports} new note(s) created.")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export Zotero items to Obsidian markdown notes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python zotero_to_obsidian.py
    python zotero_to_obsidian.py --vault ~/Documents/MyVault
    python zotero_to_obsidian.py --folder "Reading Notes"
    python zotero_to_obsidian.py --tag "to-obsidian" --remove-tag
    python zotero_to_obsidian.py --dry-run
        """,
    )
    # Non-secret defaults come from configs/config.yaml; CLI flags override.
    settings = load_settings()
    obs = settings.get("obsidian", {})
    exp = settings.get("export", {})

    parser.add_argument(
        "--vault", "-v",
        default=None,
        help="Path to Obsidian vault (default: obsidian.vault_path from config.yaml)",
    )
    parser.add_argument(
        "--folder", "-f",
        default=obs.get("notes_folder", "Papers"),
        help="Subfolder within the vault for notes "
             "(default: obsidian.notes_folder from config.yaml)",
    )
    parser.add_argument(
        "--tag", "-t",
        default=exp.get("tag", "obs"),
        help="Zotero tag to filter by (default: export.tag from config.yaml)",
    )
    parser.add_argument(
        "--remove-tag",
        action=argparse.BooleanOptionalAction,
        default=exp.get("remove_tag", False),
        help="Remove the tag from Zotero items after export "
             "(default: export.remove_tag from config.yaml)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be created without writing files",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear the export state (re-export all tagged items)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args(argv)

    if args.reset_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print("Export state cleared.")
        else:
            print("No state file found.")
        if not any([args.vault, args.dry_run]):
            return

    results = export_items(
        tag=args.tag,
        vault_path=args.vault or "",
        notes_folder=args.folder,
        remove_tag=args.remove_tag,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )

    # Exit code: 1 if any errors occurred
    if any(r["status"] == "error" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
