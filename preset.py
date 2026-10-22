#!/usr/bin/env python3
"""Backward-compatible entry point for `openrouter-routing preset`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from openrouter_routing.cli import preset_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(preset_main())
