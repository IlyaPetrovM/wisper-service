import os
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, UploadFile, HTTPException, Query
from fastapi.responses import Response, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request
import requests

from core import (
    ModelSize,
    load_model_sync,
    get_loaded_models,
    get_available_models_list,
    is_model_loaded,
    process_audio_file,
    download_and_transcribe,
    get_service_info,
    get_health_info,
    get_models_info
)

logger = logging.getLogger(__name__)

router = APIRouter()

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


class TranscribeResponse(BaseModel):
    """Модель ответа для веб-интерфейса"""
    success: bool
    logs: List[str]
    result: str = None
    format: str
    filename: str = None
    error: str = None


class LoadModelResponse(BaseModel):
    success: bool
    message: str
    model_size: str
    loaded: bool


@router.get("/", response_class=HTMLResponse)
async def serve_ui(request: Request):
    """Веб-интерфейс для транскрибирования"""
    return templates.TemplateResponse("index.html", {"request": request})


@router.post("/api/transcribe", response_model=TranscribeResponse)
async def transcribe_audio_api(
    file: UploadFile = File(None, description="Аудио файл для транскрибирования"),
    url: str = Query(None, description="URL аудио файла"),
    model_size: ModelSize = Query(
        default=ModelSize.SMALL,
        description="Размер модели Whisper"
    ),
    format: str = Query(
        default="srt",
        description="Формат ответа: json или srt",
        regex="^(json|srt)$"
    )
):
    """API endpoint для веб-интерфейса с логированием"""
    logs = []

    try:
        if not file and not url:
            raise HTTPException(status_code=400, detail="Укажите файл или URL")

        if file:
            filename = file.filename
            if not filename:
                raise HTTPException(status_code=400, detail="Некорректное имя файла")

            try:
                from core import validate_audio_extension
                if not validate_audio_extension(filename):
                    raise HTTPException(status_code=400, detail=f"Неподдерживаемый формат файла")

                file_content = await file.read()
                logs.append(f"Файл загружен: {filename}")

                result_content, info = await process_audio_file(file_content, filename, model_size, format, logs)

                return TranscribeResponse(
                    success=True,
                    logs=logs,
                    result=result_content,
                    format=format,
                    filename=Path(filename).stem
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        elif url:
            try:
                result_content, info, filename = await download_and_transcribe(url, model_size, format, logs)

                return TranscribeResponse(
                    success=True,
                    logs=logs,
                    result=result_content,
                    format=format,
                    filename=Path(filename).stem
                )
            except requests.exceptions.RequestException as e:
                logs.append(f"✗ Ошибка загрузки: {str(e)}")
                return TranscribeResponse(success=False, logs=logs, error=str(e), format=format)
            except ValueError as e:
                logs.append(f"✗ Ошибка: {str(e)}")
                return TranscribeResponse(success=False, logs=logs, error=str(e), format=format)

    except HTTPException as e:
        logs.append(f"✗ Ошибка: {e.detail}")
        return TranscribeResponse(success=False, logs=logs, error=e.detail, format=format)

    except Exception as e:
        logs.append(f"✗ Ошибка: {str(e)}")
        return TranscribeResponse(success=False, logs=logs, error=str(e), format=format)


@router.post("/api/load_model", response_model=LoadModelResponse)
async def api_load_model(
    model_size: ModelSize = Query(
        default=ModelSize.SMALL,
        description="Размер модели Whisper для загрузки"
    )
):
    """Загрузить модель Whisper"""
    success, message = load_model_sync(model_size)
    return LoadModelResponse(
        success=success,
        message=message,
        model_size=model_size,
        loaded=is_model_loaded(model_size)
    )


@router.post(
    "/transcribe",
    summary="Транскрибировать аудио файл",
    description="Загрузите аудио файл для транскрибирования",
    response_description="Транскрипция в JSON Lines или SRT формате"
)
async def transcribe_audio(
    file: UploadFile = File(description="Аудио файл для транскрибирования"),
    model_size: ModelSize = Query(
        default=ModelSize.SMALL,
        description="Размер модели Whisper: small (быстрая, менее точная), medium (балансная), large (медленная, наиболее точная)"
    ),
    format: str = Query(
        default="srt",
        description="Формат ответа: json (JSON Lines с метаданными) или srt (SRT файл с временными кодами)",
        regex="^(json|srt)$",
        example="srt"
    )
):
    """
    Транскрибирование загруженного аудио файла.

    Поддерживаемые форматы: mp3, wav, m4a, flac, ogg, opus, webm
    """
    try:
        from core import validate_audio_extension
        filename = file.filename
        if not filename:
            raise HTTPException(status_code=400, detail="Некорректное имя файла")

        if not validate_audio_extension(filename):
            raise HTTPException(
                status_code=400,
                detail=f"Неподдерживаемый формат файла. Поддерживаются: mp3, wav, m4a, flac, ogg, opus, webm"
            )

        file_content = await file.read()
        result_content, info = await process_audio_file(file_content, filename, model_size, format)

        filename_without_ext = Path(filename).stem

        if format == "srt":
            return Response(
                content=result_content,
                media_type="application/x-subrip",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename_without_ext}.srt"'
                }
            )
        else:
            return Response(
                content=result_content,
                media_type="application/x-ndjson"
            )

    except ValueError as e:
        logger.error(f"Ошибка валидации: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при транскрибировании: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибирования: {str(e)}")


@router.post(
    "/transcribe-url",
    summary="Транскрибировать аудио по URL",
    description="Укажите URL аудио файла для транскрибирования",
    response_description="Транскрипция в JSON Lines или SRT формате"
)
async def transcribe_audio_url(
    url: str = Query(description="URL аудио файла для транскрибирования", example="https://example.com/audio.mp3"),
    model_size: ModelSize = Query(
        default=ModelSize.SMALL,
        description="Размер модели Whisper: small (быстрая, менее точная), medium (балансная), large (медленная, наиболее точная)"
    ),
    format: str = Query(
        default="srt",
        description="Формат ответа: json (JSON Lines с метаданными) или srt (SRT файл с временными кодами)",
        regex="^(json|srt)$",
        example="srt"
    )
):
    """
    Транскрибирование аудио файла по URL.

    Поддерживаемые форматы: mp3, wav, m4a, flac, ogg, opus, webm
    """
    try:
        result_content, info, filename = await download_and_transcribe(url, model_size, format)

        filename_without_ext = Path(filename).stem

        if format == "srt":
            return Response(
                content=result_content,
                media_type="application/x-subrip",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename_without_ext}.srt"'
                }
            )
        else:
            return Response(
                content=result_content,
                media_type="application/x-ndjson"
            )

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при скачивании файла: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Ошибка при скачивании файла: {str(e)}")
    except ValueError as e:
        logger.error(f"Ошибка: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при транскрибировании: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибирования: {str(e)}")


@router.get("/", summary="Проверка работоспособности")
async def root():
    """Проверка работы сервиса"""
    return get_service_info()


@router.get("/health", summary="Health check")
async def health_check():
    """Проверка здоровья сервиса"""
    return get_health_info()


@router.get("/models", summary="Список доступных моделей")
async def list_models():
    """Получить информацию о доступных моделях"""
    return get_models_info()
