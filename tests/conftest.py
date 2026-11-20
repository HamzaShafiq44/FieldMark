"""Shared test configuration."""

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.services import inspection_service


@pytest.fixture(autouse=True)
def disable_automatic_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests offline; report tests exercise generation explicitly."""
    monkeypatch.setattr(inspection_service, "generate_automatic_report", lambda _: False)


@pytest.fixture(autouse=True)
def offline_groq_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never call the live Groq service from unit tests."""
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(""))
