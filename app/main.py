"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.routes_inspection import router as inspection_router

app = FastAPI(title="Visual Defect Inspector", version="0.1.0")
app.include_router(inspection_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Report that the API process is responsive."""
    return {"status": "ok"}
