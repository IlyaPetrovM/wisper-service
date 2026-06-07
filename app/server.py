import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fastapi_routes import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Создание и конфигурация FastAPI приложения"""
    app = FastAPI(
        title="Whisper Transcription Service",
        description="Сервис для транскрибирования русскоязычного аудио в SRT формат",
        version="1.0.0"
    )

    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    templates_dir.mkdir(exist_ok=True)
    static_dir.mkdir(exist_ok=True)

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(router)

    @app.on_event("startup")
    async def startup_event():
        logger.info("Сервис транскрибации инициализирован (модели загружаются по требованию)")

    return app
