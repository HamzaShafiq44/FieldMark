"""HTTP tests for product category management."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.schemas import Condition, Finding, InspectionResult
from app.api import routes_inspection
from app.core.config import settings
from app.main import app
from app.services import inspection_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_category_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep category tests isolated from local application data."""
    monkeypatch.setattr(settings, "inspection_db_path", tmp_path / "inspections.sqlite3")


def _list() -> list[dict[str, object]]:
    response = client.get("/api/categories")
    assert response.status_code == 200
    return response.json()["items"]


def _finding(produce_type: str) -> Finding:
    return Finding(
        produce_type=produce_type,
        detected_item=produce_type,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
    )


def test_defaults_are_seeded_once() -> None:
    """Seed the original four categories on first access."""
    first = _list()
    second = _list()
    assert [item["slug"] for item in first] == ["tomato", "apple", "banana", "orange"]
    assert len(first) == len(second) == 4


def test_create_and_rename_category() -> None:
    """Create a category and rename its display name without changing its slug."""
    response = client.post("/api/categories", json={"name": "Mango", "emoji": "🥭"})
    assert response.status_code == 201
    created = response.json()
    assert created["slug"] == "mango"
    assert created["display_name"] == "Mango"
    assert created["is_active"] is True

    renamed = client.patch("/api/categories/mango", json={"name": "Alphonso Mango"})
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Alphonso Mango"
    assert renamed.json()["slug"] == "mango"


def test_duplicate_and_reserved_names_are_rejected() -> None:
    """Reject duplicate slugs and the reserved unknown category."""
    assert client.post("/api/categories", json={"name": "Apple"}).status_code == 409
    assert client.post("/api/categories", json={"name": "Unknown"}).status_code == 400
    assert client.post("/api/categories", json={"name": "A"}).status_code == 422


def test_disabled_category_is_excluded_from_new_inspections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use only active categories when grading a new inspection."""
    assert client.post("/api/categories", json={"name": "Mango", "emoji": "🥭"}).status_code == 201

    def inspect(data: bytes) -> InspectionResult:
        return inspection_service.inspect_produce(data, detect=lambda _: _finding("mango"))

    monkeypatch.setattr(routes_inspection, "inspect_produce", inspect)
    active = client.post("/inspections/product", files={"image": ("mango.jpg", _jpeg())})
    assert active.status_code == 200
    assert active.json()["in_inspection_categories"] is True

    assert client.patch("/api/categories/mango", json={"is_active": False}).status_code == 200
    disabled = client.post("/inspections/product", files={"image": ("mango.jpg", _jpeg())})
    assert disabled.status_code == 200
    assert disabled.json()["produce_type"] == "unknown"
    assert disabled.json()["in_inspection_categories"] is False
    assert disabled.json()["grade"] == "A"


def test_delete_in_use_category_requires_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to delete a category referenced by saved inspections."""
    assert client.post("/api/categories", json={"name": "Mango"}).status_code == 201

    def inspect(data: bytes) -> InspectionResult:
        return inspection_service.inspect_produce(data, detect=lambda _: _finding("mango"))

    monkeypatch.setattr(routes_inspection, "inspect_produce", inspect)
    saved = client.post("/inspections/product", files={"image": ("mango.jpg", _jpeg())})
    assert saved.status_code == 200

    response = client.delete("/api/categories/mango")
    assert response.status_code == 409
    assert "Disable" in response.json()["detail"]


def test_delete_unused_category() -> None:
    """Delete a category that has no saved inspections."""
    assert client.post("/api/categories", json={"name": "Guava"}).status_code == 201
    assert client.delete("/api/categories/guava").status_code == 204
    assert "guava" not in {item["slug"] for item in _list()}


def test_last_active_category_cannot_be_disabled() -> None:
    """Keep at least one active category for future inspections."""
    for slug in ("tomato", "apple", "banana"):
        assert client.patch(f"/api/categories/{slug}", json={"is_active": False}).status_code == 200
    response = client.patch("/api/categories/orange", json={"is_active": False})
    assert response.status_code == 400
    assert "At least one active category" in response.json()["detail"]


def test_unknown_category_returns_404() -> None:
    """Return a clear not-found response for unknown slugs."""
    assert client.patch("/api/categories/missing", json={"name": "Missing"}).status_code == 404
    assert client.delete("/api/categories/missing").status_code == 404


def _jpeg() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (16, 16), color="green").save(buffer, format="JPEG")
    return buffer.getvalue()
