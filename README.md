# Alexandria

A two-stage research-paper pipeline between **Zotero** and **Obsidian**:

1. **Import** — scan a folder of PDFs, detect which are research papers
   (heuristic confidence score), extract DOI / arXiv / title, look up full
   metadata via CrossRef / OpenAlex, create a Zotero item, attach the PDF, and
   move the renamed PDF into a sorted folder.
2. **Export** — pull Zotero items carrying a tag (default `obs`) and write an
   Obsidian markdown note for each (YAML frontmatter, Zotero / Advanced-URI
   buttons, Dataview reading-notes block). Already-exported items are tracked in
   `.obsidian_export_state.json` so re-runs don't duplicate notes.

This project was consolidated from standalone scripts that previously lived in
`~/Stacks/Scripts/`.

## Layout

```
app/
  config.py           # shared: resolves paths, loads .env + config.yaml
  paper_importer.py   # stage 1: PDFs -> Zotero
  paper_exporter.py   # stage 2: Zotero -> Obsidian notes
configs/
  config.example.yaml # template -> copy to config.yaml
  config.yaml         # your settings (vault path, dirs, thresholds); gitignored
  .env                # Zotero API credentials (gitignored)
  .env.example        # template for .env
.obsidian_export_state.json   # export state (machine-local, gitignored)
```

## Setup

```bash
cd ~/dev/alexandria
uv sync                       # create .venv and install deps
cp configs/config.example.yaml configs/config.yaml  # then edit paths
cp configs/.env.example configs/.env               # then fill in Zotero keys
```

Edit `configs/config.yaml` for your vault path, source/output folders,
detection threshold, and tags. Get Zotero API credentials from
<https://www.zotero.org/settings/keys>.

## Usage

```bash
# Import every PDF in the configured source_dir into Zotero
uv run python app/paper_importer.py

# ...or point at a specific file/folder and override settings
uv run python app/paper_importer.py ~/Downloads/paper.pdf --threshold 40 --dry-run

# Export tagged Zotero items to Obsidian notes
uv run python app/paper_exporter.py

# Preview the export without writing files
uv run python app/paper_exporter.py --dry-run
```

CLI flags always override `config.yaml`. The shell aliases `importpapers`,
`exportpapers`, and `library` (run both) point at this project.
