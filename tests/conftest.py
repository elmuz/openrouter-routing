from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


@pytest.fixture
def session_paths() -> list[str]:
    return sorted(str(path) for path in FIXTURES.rglob("*.jsonl"))


@pytest.fixture
def alpha_path() -> str:
    return str(next((FIXTURES / "--home-alice-projects-alpha--").glob("*.jsonl")))
