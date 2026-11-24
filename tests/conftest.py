from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


@pytest.fixture(autouse=True)
def _isolate_credentials(monkeypatch, tmp_path):
    """Never read the developer's real credentials or keys during tests."""
    monkeypatch.setenv("OPENROUTER_ROUTING_CREDENTIALS", str(tmp_path / "credentials"))
    monkeypatch.setenv("OPENROUTER_ROUTING_ENV", str(tmp_path / "env"))
    monkeypatch.delenv("OPENROUTER_MANAGEMENT_KEY", raising=False)


@pytest.fixture
def session_paths() -> list[str]:
    return sorted(str(path) for path in FIXTURES.rglob("*.jsonl"))


@pytest.fixture
def alpha_path() -> str:
    return str(next((FIXTURES / "--home-alice-projects-alpha--").glob("*.jsonl")))
