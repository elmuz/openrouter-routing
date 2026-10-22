#!/usr/bin/env python3
"""Backward-compatible entry point for `openrouter-routing cheapest`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from openrouter_routing.cli import cheapest_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cheapest_main())
