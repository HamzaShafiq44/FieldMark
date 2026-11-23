"""Shared test configuration."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.ai import client_factory
from app.api import routes_inspection
from app.core.config import settings
from app.services import inspection_service, job_service


@pytest.fixture(autouse=True)
def reset_job_registry() -> Iterator[None]:
    """Isolate the in-memory job registry between tests."""
    job_service.clear_jobs()
    yield
    job_service.clear_jobs()


@pytest.fixture(autouse=True)
def disable_job_finalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep job tests offline; job-specific tests may override this."""
    monkeypatch.setattr(job_service, "finalize_inspection", lambda _: None)


@pytest.fixture(autouse=True)
def disable_automatic_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests offline; report tests exercise generation explicitly."""
    monkeypatch.setattr(inspection_service, "generate_automatic_report", lambda _: False)


@pytest.fixture(autouse=True)
def disable_background_finalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep deferred localization and reporting out of HTTP unit tests."""
    monkeypatch.setattr(routes_inspection, "finalize_inspection", lambda _: None)


@pytest.fixture(autouse=True)
def offline_groq_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never call the live Groq service from unit tests."""
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(""))


@pytest.fixture(autouse=True)
def reset_ai_client_cache() -> Iterator[None]:
    """Isolate cached SDK clients between tests."""
    client_factory.clear_client_cache()
    yield
    client_factory.clear_client_cache()
