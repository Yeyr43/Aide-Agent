"""Locale data — loaded from JSON files at module init.

Domain files:
  prompts.json   — soul / tools prompt / template strings
  commands.json  — command descriptions and messages
  ui.json        — UI labels, onboarding, bridge, widgets
  runtime.json   — tool descriptions, memory, context, MCP, LLM gateway

Each JSON file is a dict of {key: {zh: str, en: str}}.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent

_STRINGS: dict[str, dict[str, str]] = {}

for _name in ["prompts", "commands", "ui", "runtime"]:
    _path = _DATA_DIR / f"{_name}.json"
    if _path.exists():
        try:
            _data = json.loads(_path.read_text(encoding="utf-8"))
            _STRINGS.update(_data)
        except (json.JSONDecodeError, OSError):
            # Corrupt or missing JSON — skip gracefully
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load locale file: %s", _path,
            )
