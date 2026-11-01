"""Shared configuration for the Alexandria paper pipeline.

Secrets live in ``<config-home>/.env`` and non-secret settings in
``<config-home>/config.yaml``. Both the importer and exporter use this module
so there is a single place that knows where things live.

The config home is resolved once, in this order:

1. ``$ALEXANDRIA_CONFIG`` if set (point it at any directory you like).
2. ``$XDG_CONFIG_HOME/alexandria`` if ``XDG_CONFIG_HOME`` is set.
3. ``~/.config/alexandria`` otherwise.

This keeps config independent of where the code lives, so a globally
installed ``alexandria`` (``uv tool install``) and a source checkout both read
the same files. On first use, settings from a sibling ``configs/`` directory
(i.e. running from a source checkout) are migrated into the config home.
"""

import os
import shutil
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None

# alexandria/config.py -> repo root is one level up from the package dir.
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent


def config_home() -> Path:
    """Return the directory that holds config.yaml / .env / export state."""
    env = os.environ.get("ALEXANDRIA_CONFIG")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "alexandria"


CONFIG_HOME = config_home()
ENV_FILE = CONFIG_HOME / ".env"
CONFIG_FILE = CONFIG_HOME / "config.yaml"
STATE_FILE = CONFIG_HOME / "export_state.json"

# Templates shipped so ``alexandria config init`` works even when installed as
# a tool with no source checkout nearby.
CONFIG_EXAMPLE = """\
# Alexandria — paper pipeline configuration
#
# Non-secret settings only. Zotero API credentials live in .env (see
# .env.example). CLI flags on the import/export commands override these.

zotero:
  # "user" or "group"
  library_type: user

obsidian:
  # Path to your Obsidian vault
  vault_path: ~/path/to/your/Vault
  # Subfolder within the vault where paper notes are written
  notes_folder: Papers

import:
  # Folder scanned for incoming PDFs
  source_dir: ~/Documents/Unsorted-PDFs
  # Imported PDFs are renamed to their title and moved here
  output_dir: ~/Documents/Sorted-Papers
  # Detection confidence threshold (0-100); below this a PDF is skipped
  threshold: 25
  # Tag applied to newly created Zotero items (the exporter looks for this)
  tag: obs

export:
  # Zotero tag whose items get exported to Obsidian
  tag: obs
  # Remove the tag from the Zotero item after a successful export
  remove_tag: true
"""

ENV_EXAMPLE = """\
# Zotero API credentials. Get these from https://www.zotero.org/settings/keys
ZOTERO_LIBRARY_ID=your_user_id
ZOTERO_API_KEY=your_api_key
"""


def _migrate_from_checkout() -> None:
    """One-time migration: if running from a source checkout that still has a
    ``configs/`` dir with real files, copy them into the config home so an
    existing setup keeps working after the move to ~/.config/alexandria.
    """
    legacy = REPO_ROOT / "configs"
    if not legacy.is_dir():
        return
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    pairs = [
        (legacy / "config.yaml", CONFIG_FILE),
        (legacy / ".env", ENV_FILE),
    ]
    for src, dst in pairs:
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    # Legacy export state lived at the repo root.
    legacy_state = REPO_ROOT / ".obsidian_export_state.json"
    if legacy_state.exists() and not STATE_FILE.exists():
        shutil.copy2(legacy_state, STATE_FILE)


def init_config(force: bool = False) -> dict:
    """Create the config home and seed config.yaml / .env from templates.

    First tries to migrate an existing source-checkout ``configs/`` dir; then
    writes templates for anything still missing. Returns a dict describing what
    happened for each file: "migrated", "created", or "exists".
    """
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    before = {CONFIG_FILE: CONFIG_FILE.exists(), ENV_FILE: ENV_FILE.exists()}
    _migrate_from_checkout()

    result = {}
    for path, template, key in (
        (CONFIG_FILE, CONFIG_EXAMPLE, "config.yaml"),
        (ENV_FILE, ENV_EXAMPLE, ".env"),
    ):
        if path.exists() and not force:
            result[key] = "migrated" if not before[path] else "exists"
        else:
            path.write_text(template)
            result[key] = "created"
    return result


def ensure_config() -> None:
    """Make sure config.yaml exists, migrating/seeding it if not."""
    if not CONFIG_FILE.exists():
        init_config()


def load_env() -> None:
    """Load ``KEY=VALUE`` pairs from <config-home>/.env into ``os.environ``.

    Existing environment variables win (uses ``setdefault``), so you can still
    override a value from the shell. Migrates from a source checkout on first
    use if nothing is present yet.
    """
    if not ENV_FILE.exists():
        _migrate_from_checkout()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def load_settings() -> dict:
    """Return the parsed <config-home>/config.yaml (empty dict if missing)."""
    if yaml is None:
        raise RuntimeError("pyyaml is required — run `uv sync`.")
    if not CONFIG_FILE.exists():
        _migrate_from_checkout()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


def expand(path: str | None) -> str:
    """Expand ``~`` and environment variables in a config path value."""
    if not path:
        return ""
    return os.path.expanduser(os.path.expandvars(path))
