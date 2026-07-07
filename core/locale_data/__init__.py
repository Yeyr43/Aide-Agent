"""Locale data — merged from domain files."""

from ._prompts import _STRINGS as _prompts
from ._commands import _STRINGS as _commands
from ._ui import _STRINGS as _ui
from ._runtime import _STRINGS as _runtime

_STRINGS: dict[str, dict[str, str]] = {}
_STRINGS.update(_prompts)
_STRINGS.update(_commands)
_STRINGS.update(_ui)
_STRINGS.update(_runtime)
