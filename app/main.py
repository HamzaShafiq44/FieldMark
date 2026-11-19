"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_categories import router as category_router
from app.api.routes_inspection import router as inspection_router

app = FastAPI(title="Visual Defect Inspector", version="0.1.0")
app.include_router(inspection_router)
app.include_router(category_router)

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@app.get("/health")
async def health() -> dict[str, str]:
    """Report that the API process is responsive."""
    return {"status": "ok"}


@app.get("/categories", include_in_schema=False)
async def categories_page() -> FileResponse:
    """Serve the single-page app for the categories route."""
    return FileResponse(FRONTEND_DIST / "index.html")


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
