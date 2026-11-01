"""Shared configuration for the Alexandria paper pipeline.

Resolves paths relative to the project root, loads secrets from
``configs/.env``, and non-secret settings from ``configs/config.yaml``.
Both the importer and exporter use this module so there is a single place
that knows where things live.
"""

import os
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None

# app/config.py -> project root is one level up from app/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
ENV_FILE = CONFIGS_DIR / ".env"
CONFIG_FILE = CONFIGS_DIR / "config.yaml"

# Export state lives at the project root (machine-local, gitignored).
STATE_FILE = PROJECT_ROOT / ".obsidian_export_state.json"


def load_env() -> None:
    """Load ``KEY=VALUE`` pairs from configs/.env into ``os.environ``.

    Existing environment variables win (uses ``setdefault``), so you can
    still override a value from the shell.
    """
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def load_settings() -> dict:
    """Return the parsed ``configs/config.yaml`` (empty dict if missing)."""
    if yaml is None:
        raise RuntimeError("pyyaml is required — run `uv sync`.")
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


def expand(path: str | None) -> str:
    """Expand ``~`` and environment variables in a config path value."""
    if not path:
        return ""
    return os.path.expanduser(os.path.expandvars(path))
