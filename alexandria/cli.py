"""Unified ``alexandria`` command-line entry point.

A thin dispatcher over the existing import / export / backfill modules plus the
Textual TUI. Each subcommand keeps its own argparse parser, so
``alexandria import --help`` etc. show the real per-command options.

    alexandria import [path] [--threshold N] [--dry-run] ...
    alexandria export [--dry-run] [--folder ...] ...
    alexandria library [--dry-run] [--quiet]      # import then export
    alexandria backfill [--apply]
    alexandria tui
    alexandria config {path,init,edit}
"""

import argparse
import os
import subprocess
import sys

from alexandria import __version__
from alexandria import config as cfg

USAGE = """\
alexandria — Zotero <-> Obsidian paper pipeline

usage: alexandria <command> [options]

commands:
  import     Detect research PDFs and import them into Zotero
  export     Export tagged Zotero items to Obsidian notes
  library    Run import then export (the full pipeline)
  backfill   Attach stored PDFs to link-only Zotero items
  tui        Launch the interactive terminal UI
  config     Show or initialise the config files

Run `alexandria <command> --help` for a command's options.
"""


def _run(mainfn, argv):
    """Call a module ``main(argv)`` and normalise its exit code.

    The modules call ``sys.exit(...)``; catch that so chaining (library) and
    the top-level exit code both work.
    """
    try:
        mainfn(argv)
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1


def _cmd_library(argv):
    """Import then export, forwarding the shared --dry-run/--quiet flags."""
    parser = argparse.ArgumentParser(
        prog="alexandria library",
        description="Run the full pipeline: import PDFs into Zotero, "
                    "then export tagged items to Obsidian.",
    )
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Pass --dry-run to both stages (writes nothing)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress progress output in both stages")
    args = parser.parse_args(argv)

    shared = []
    if args.dry_run:
        shared.append("--dry-run")
    if args.quiet:
        shared.append("--quiet")

    from alexandria import importer, exporter

    print("=== Import ===")
    rc_import = _run(importer.main, list(shared))
    print("\n=== Export ===")
    rc_export = _run(exporter.main, list(shared))
    return rc_import or rc_export


def _cmd_config(argv):
    parser = argparse.ArgumentParser(
        prog="alexandria config",
        description="Inspect or create the Alexandria config files.",
    )
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("path", help="Print the config home directory")
    p_init = sub.add_parser("init", help="Create config.yaml / .env from templates")
    p_init.add_argument("--force", action="store_true",
                        help="Overwrite existing files with fresh templates")
    sub.add_parser("edit", help="Open config.yaml in $EDITOR")
    args = parser.parse_args(argv)

    action = args.action or "path"

    if action == "path":
        print(cfg.CONFIG_HOME)
        print(f"  config.yaml : {cfg.CONFIG_FILE}"
              f"{'' if cfg.CONFIG_FILE.exists() else '  (missing)'}")
        print(f"  .env        : {cfg.ENV_FILE}"
              f"{'' if cfg.ENV_FILE.exists() else '  (missing)'}")
        print(f"  state       : {cfg.STATE_FILE}"
              f"{'' if cfg.STATE_FILE.exists() else '  (missing)'}")
        return 0

    if action == "init":
        result = cfg.init_config(force=args.force)
        print(f"Config home: {cfg.CONFIG_HOME}")
        for name, what in result.items():
            print(f"  {name}: {what}")
        if "created" in result.values():
            print("\nEdit the files above — set your Zotero keys in .env and "
                  "your vault/folder paths in config.yaml.")
        return 0

    if action == "edit":
        cfg.ensure_config()
        editor = os.environ.get("EDITOR", "vi")
        return subprocess.call([editor, str(cfg.CONFIG_FILE)])

    parser.print_help()
    return 2


def main():
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"alexandria {__version__}")
        return 0

    command, rest = argv[0], argv[1:]

    if command == "import":
        from alexandria import importer
        return _run(importer.main, rest)
    if command == "export":
        from alexandria import exporter
        return _run(exporter.main, rest)
    if command == "backfill":
        from alexandria import backfill
        return _run(backfill.main, rest)
    if command == "library":
        return _cmd_library(rest)
    if command == "config":
        return _cmd_config(rest)
    if command == "tui":
        from alexandria import tui
        return tui.run()

    print(f"alexandria: unknown command '{command}'\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
