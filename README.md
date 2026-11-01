# Alexandria

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Python](https://img.shields.io/badge/python-3.13-blue.svg) ![uv](https://img.shields.io/badge/managed%20with-uv-6340ac.svg)

A two-stage research-paper pipeline between **Zotero** and **Obsidian**, exposed
as a single `alexandria` command with a CLI and a Textual TUI.

1. **Import** — scan a folder of PDFs, detect which are research papers
   (heuristic confidence score), extract DOI / arXiv / title, look up full
   metadata via CrossRef / OpenAlex, create a Zotero item, attach the PDF, and
   move the renamed PDF into a sorted folder.
2. **Export** — pull Zotero items carrying a tag (default `obs`) and write an
   Obsidian markdown note for each (YAML frontmatter, Zotero / Advanced-URI
   buttons, Dataview reading-notes block). Already-exported items are tracked in
   the export state so re-runs don't duplicate notes.

## Install

Install it as a global tool — no shell aliases required:

```bash
uv tool install ~/dev/alexandria     # provides the `alexandria` command on PATH
# later: uv tool upgrade alexandria  (after pulling changes / bumping version)
```

Or run from a source checkout during development:

```bash
git clone https://github.com/bgraham1996/alexandria.git
cd alexandria
uv sync
uv run alexandria --help
```

## Configure

Config and secrets live in **`~/.config/alexandria/`** (override with
`$ALEXANDRIA_CONFIG`), independent of where the code is installed:

```bash
alexandria config init    # create config.yaml + .env from templates
alexandria config edit    # open config.yaml in $EDITOR
alexandria config path    # show the config home and file status
```

- `config.yaml` — non-secret settings: vault path, source/output folders,
  detection threshold, tags.
- `.env` — Zotero API credentials (`ZOTERO_LIBRARY_ID`, `ZOTERO_API_KEY`), from
  <https://www.zotero.org/settings/keys>.

CLI flags always override `config.yaml`.

## Usage

```bash
alexandria import                      # import every PDF in source_dir
alexandria import ~/Downloads/p.pdf --threshold 40 --dry-run
alexandria export                      # export tagged Zotero items to Obsidian
alexandria export --dry-run
alexandria library                     # import then export (the full pipeline)
alexandria library --dry-run
alexandria backfill                    # dry-run: find link-only items to fix
alexandria backfill --apply            # attach the matched PDFs
alexandria tui                         # interactive terminal UI
```

### TUI

`alexandria tui` opens a dashboard showing the resolved configuration and PDF
count, with buttons (or keys `i`/`e`/`l`) to run import, export, or the full
library pipeline — output streams into a log pane. Toggle **Dry run** to preview
without writing.

## Layout

```
alexandria/
  __init__.py
  config.py     # config home (~/.config/alexandria), .env + config.yaml, migration
  cli.py        # the `alexandria` entry point (argparse dispatcher)
  tui.py        # Textual terminal UI
  importer.py   # stage 1: PDFs -> Zotero
  exporter.py   # stage 2: Zotero -> Obsidian notes
  backfill.py   # repair tool: attach PDFs to link-only items
```

This project was consolidated from standalone scripts that previously lived in
`~/Stacks/Scripts/`.

## License

[MIT](LICENSE) © Ben Graham
