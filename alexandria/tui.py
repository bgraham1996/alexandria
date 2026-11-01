"""Interactive terminal UI for Alexandria (Textual).

A small dashboard: shows the resolved configuration, lets you run the import /
export / library stages with a click, and streams their output into a log
pane. Launch with ``alexandria tui``.
"""

import io
import sys
from pathlib import Path

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Checkbox, Footer, Header, RichLog, Static
except ImportError:  # pragma: no cover
    App = None


from alexandria import config as cfg


def _gather_status() -> dict:
    """Collect a snapshot of the current configuration for display."""
    cfg.load_env()
    settings = cfg.load_settings()
    imp = settings.get("import", {})
    obs = settings.get("obsidian", {})
    exp = settings.get("export", {})

    import os
    source = cfg.expand(imp.get("source_dir"))
    pdf_count = None
    if source and Path(source).is_dir():
        pdf_count = len(list(Path(source).glob("*.pdf")))

    return {
        "config_home": str(cfg.CONFIG_HOME),
        "keys_set": bool(os.environ.get("ZOTERO_LIBRARY_ID")
                         and os.environ.get("ZOTERO_API_KEY")),
        "source": source or "(unset)",
        "pdf_count": pdf_count,
        "output": cfg.expand(imp.get("output_dir")) or "(unset)",
        "vault": cfg.expand(obs.get("vault_path")) or "(unset)",
        "notes": obs.get("notes_folder", "Papers"),
        "import_tag": imp.get("tag", "obs"),
        "export_tag": exp.get("tag", "obs"),
        "threshold": imp.get("threshold", 45),
    }


def _status_markup(s: dict) -> str:
    keys = "[green]set[/]" if s["keys_set"] else "[red]NOT set[/] (edit .env)"
    pdfs = "" if s["pdf_count"] is None else f"  [cyan]{s['pdf_count']} PDF(s)[/]"
    return (
        f"[b]Config home[/]  {s['config_home']}\n"
        f"[b]Zotero keys[/]  {keys}\n"
        f"[b]Source[/]       {s['source']}{pdfs}\n"
        f"[b]Output[/]       {s['output']}\n"
        f"[b]Vault[/]        {s['vault']}  →  {s['notes']}/\n"
        f"[b]Tags[/]         import=[yellow]{s['import_tag']}[/] "
        f"export=[yellow]{s['export_tag']}[/]   "
        f"[b]Threshold[/] {s['threshold']}"
    )


if App is not None:

    class _LogWriter(io.TextIOBase):
        """File-like object that streams written text into a RichLog widget."""

        def __init__(self, app, log):
            self._app = app
            self._log = log
            self._buf = ""

        def write(self, text):
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._app.call_from_thread(self._log.write, line)
            return len(text)

        def flush(self):
            if self._buf:
                self._app.call_from_thread(self._log.write, self._buf)
                self._buf = ""

    class AlexandriaApp(App):
        TITLE = "Alexandria"
        SUB_TITLE = "Zotero ↔ Obsidian paper pipeline"
        CSS = """
        #status { padding: 1 2; border: round $primary; height: auto; margin: 1 1 0 1; }
        #controls { height: auto; padding: 0 1; }
        #controls Button { margin: 1 1 0 0; }
        #dry { margin: 1 1 0 1; }
        RichLog { border: round $secondary; margin: 1; }
        """
        BINDINGS = [
            ("i", "run('import')", "Import"),
            ("e", "run('export')", "Export"),
            ("l", "run('library')", "Library"),
            ("r", "refresh_status", "Refresh"),
            ("q", "quit", "Quit"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static(id="status")
            with Horizontal(id="controls"):
                yield Button("Import", id="import", variant="primary")
                yield Button("Export", id="export", variant="primary")
                yield Button("Library", id="library", variant="success")
                yield Button("Backfill (dry-run)", id="backfill")
                yield Button("Refresh", id="refresh")
            yield Checkbox("Dry run", id="dry")
            yield RichLog(id="log", highlight=True, markup=False, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            self.refresh_status()
            self.query_one("#log", RichLog).write("Ready. Pick a stage above.")

        def refresh_status(self) -> None:
            self.action_refresh_status()

        def action_refresh_status(self) -> None:
            try:
                s = _gather_status()
                self.query_one("#status", Static).update(_status_markup(s))
            except Exception as e:  # noqa: BLE001
                self.query_one("#status", Static).update(f"[red]Config error: {e}[/]")

        def on_button_pressed(self, event: "Button.Pressed") -> None:
            bid = event.button.id
            if bid == "refresh":
                self.action_refresh_status()
                self.query_one("#log", RichLog).write("— status refreshed —")
            elif bid in ("import", "export", "library", "backfill"):
                self.action_run(bid)

        def action_run(self, stage: str) -> None:
            dry = self.query_one("#dry", Checkbox).value
            log = self.query_one("#log", RichLog)
            self._set_buttons(disabled=True)
            label = stage + (" (dry-run)" if dry and stage != "backfill" else "")
            log.write(f"\n$ alexandria {label}\n{'=' * 40}")
            self.run_worker(
                lambda: self._run_stage(stage, dry),
                thread=True,
                exclusive=True,
                name=stage,
            )

        def _run_stage(self, stage: str, dry: bool) -> None:
            """Runs in a worker thread: redirect stdout and invoke the stage."""
            from alexandria.cli import _run

            log = self.query_one("#log", RichLog)
            writer = _LogWriter(self, log)
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = writer
            try:
                if stage == "import":
                    from alexandria import importer
                    _run(importer.main, ["--dry-run"] if dry else [])
                elif stage == "export":
                    from alexandria import exporter
                    _run(exporter.main, ["--dry-run"] if dry else [])
                elif stage == "library":
                    from alexandria import importer, exporter
                    extra = ["--dry-run"] if dry else []
                    print("=== Import ===")
                    _run(importer.main, list(extra))
                    print("\n=== Export ===")
                    _run(exporter.main, list(extra))
                elif stage == "backfill":
                    from alexandria import backfill
                    _run(backfill.main, [])  # dry-run only from the TUI
            except Exception as e:  # noqa: BLE001
                print(f"[error] {e}")
            finally:
                writer.flush()
                sys.stdout, sys.stderr = old_out, old_err
                self.call_from_thread(self._stage_done, stage)

        def _stage_done(self, stage: str) -> None:
            self.query_one("#log", RichLog).write(f"{'=' * 40}\n✓ {stage} finished")
            self._set_buttons(disabled=False)
            self.action_refresh_status()

        def _set_buttons(self, disabled: bool) -> None:
            for bid in ("import", "export", "library", "backfill"):
                self.query_one(f"#{bid}", Button).disabled = disabled


def run() -> int:
    """Launch the TUI. Returns a process exit code."""
    if App is None:
        print("The TUI needs Textual. Run `uv sync` (or `uv tool install` the "
              "project) to install it.", file=sys.stderr)
        return 1
    cfg.ensure_config()
    AlexandriaApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(run())
