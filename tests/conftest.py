"""Shared test configuration."""

import pytest

from app.services import inspection_service


@pytest.fixture(autouse=True)
def disable_automatic_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests offline; report tests exercise generation explicitly."""
    monkeypatch.setattr(inspection_service, "generate_automatic_report", lambda _: False)
