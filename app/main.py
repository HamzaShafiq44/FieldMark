"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.model_info import describe_models
from app.ai.prompt_config import validate_prompt_templates
from app.api.routes_categories import router as category_router
from app.api.routes_inspection import router as inspection_router
from app.core.config import settings
from app.services.job_service import cleanup_uploads

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate prompts and discard uploads from jobs lost in a previous process."""
    validate_prompt_templates()
    cleanup_uploads()
    LOGGER.info("AI models configured: %s", describe_models())
    yield


app = FastAPI(title="Visual Defect Inspector", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inspection_router)
app.include_router(category_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Report that the API process is responsive."""
    return {"status": "ok"}
