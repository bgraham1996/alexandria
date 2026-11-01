# TICKET-001 — Self-hosted WebDAV storage for Zotero attachments

- **Status:** Backlog / Deferred
- **Priority:** Low (not currently blocking — paid Zotero storage purchased)
- **Created:** 2026-11-01
- **Area:** infrastructure / storage

## Context
The import pipeline (`app/paper_importer.py`) uploads each PDF to Zotero as an
`imported_file` attachment (a *stored* file), which consumes the Zotero
file-storage quota. The library's PDF corpus is already **~2.1 GB across 241
PDFs** and grows with every import.

On 2026-11-01 this exceeded the 2 GB Zotero plan — uploads began failing with
`File would exceed quota (2007.8 > 2000)`, which silently produced "link-only"
items (a Zotero entry with only the origin URL, no offline/annotatable PDF).
That failure mode is now surfaced loudly by the upload-verification added to
`paper_importer.py`, but the underlying constraint remains.

**Interim resolution:** upgraded/paid for additional Zotero storage, so this is
not blocking right now.

## Problem
Paid Zotero storage is a recurring cost and still capped, while the corpus grows
continuously. A free, effectively-unlimited backend that keeps the pipeline
unchanged would be preferable long term.

## Proposed solution
Use a **self-hosted WebDAV server on the existing LAN Ubuntu box
(`bg@10.0.0.1`)** as Zotero's file-sync backend instead of Zotero storage.

- Free and effectively unlimited (bounded only by the server's disk).
- **No importer changes required** — Zotero still creates `imported_file`
  attachments; it just syncs the files to our WebDAV server rather than Zotero
  storage. Offline reading + annotation continue to work on the desktop app.

## Tasks
- [ ] Stand up a WebDAV endpoint on `10.0.0.1` (nginx, or Apache `mod_dav`),
      scoped to a dedicated `zotero/` directory, with auth (and TLS if ever
      exposed beyond the LAN).
- [ ] Configure Zotero → Preferences → Sync → File Syncing → "My Library"
      to WebDAV; enter URL/user/pass; **Verify Server**.
- [ ] First sync migrates existing stored files (Zotero re-uploads them to the
      WebDAV server).
- [ ] Validate: offline open + annotate works; a fresh `importpapers` run
      attaches a PDF and `paper_importer.py`'s `_pdf_stored` verification passes
      with no quota error.
- [ ] Add the server dir to a backup routine (it becomes the sole copy of the
      attachment files).
- [ ] Document the server setup + exact Zotero sync settings (README or
      `project/design/`).

## Caveats / non-goals
- WebDAV is **personal-library only** — Zotero group libraries cannot use it.
- The **Zotero iOS/mobile app does not support WebDAV** file sync (Zotero
  storage only). If mobile reading is needed, keep paid Zotero storage for that
  or accept desktop-only file access.
- Backups become our responsibility once files live only on the LAN box.

## Acceptance criteria
- New imports attach PDFs that are retrievable offline and annotatable, with no
  Zotero quota consumption.
- Pre-existing attachments are migrated and accessible.
- The server setup and Zotero settings are documented and reproducible.

## References
- Zotero storage pricing (2026-11-01): 300 MB free · 2 GB $20/yr · 6 GB $60/yr ·
  Unlimited $120/yr.
- Zotero sync docs: https://www.zotero.org/support/sync
- Related code: `app/paper_importer.py` (upload + `_pdf_stored` verification),
  `configs/config.yaml`.
