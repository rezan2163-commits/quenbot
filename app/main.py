"""QuenBot – FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="QuenBot Dashboard",
    description="Crypto trading dashboard with real market data",
    version="1.0.0",
)

app.include_router(api_router)

TEMPLATES_DIR = Path(__file__).parent / "templates"


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard page."""
    html_path = TEMPLATES_DIR / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
