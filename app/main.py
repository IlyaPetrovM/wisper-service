import os
import json
import tempfile
from pathlib import Path
from typing import Dict, Generator, List
from enum import Enum

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import Response, StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from faster_whisper import WhisperModel
import logging
import requests
from starlette.requests import Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Whisper Transcription Service",
    description="Сервис для транскрибирования русскоязычного аудио в SRT формат",
    version="1.0.0"
)

# Setup templates and static files
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

templates_dir.mkdir(exist_ok=True)
static_dir.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(templates_dir))

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Доступные модели
class ModelSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

# Хранилище загруженных моделей
models: Dict[str, WhisperModel] = {}


def load_model(model_size: str) -> WhisperModel:
    """Загрузка конкретной модели Whisper"""
    logger.info(f"Загрузка модели Whisper ({model_size})...")

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",  # Оптимизация для CPU
        download_root=str(Path(__file__).parent / "models")
    )

    logger.info(f"Модель {model_size} загружена успешно")
    return model


@app.on_event("startup")
async def startup_event():
    """Инициализация сервиса без предварительной загрузки моделей"""
    logger.info("Сервис транскрибации инициализирован (модели загружаются по требованию)")


def format_timestamp(seconds: float) -> str:
    """Форматирование временной метки в формат SRT (HH:MM:SS,mmm)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments) -> str:
    """Генерация SRT файла из сегментов транскрипции"""
    srt_content = []

    for i, segment in enumerate(segments, start=1):
        start_time = format_timestamp(segment.start)
        end_time = format_timestamp(segment.end)
        text = segment.text.strip()

        srt_content.append(f"{i}")
        srt_content.append(f"{start_time} --> {end_time}")
        srt_content.append(text)
        srt_content.append("")  # Пустая строка между сегментами

    return "\n".join(srt_content)


def json_lines_generator(filename: str, segments, info) -> Generator[str, None, None]:
    """Генерирование JSON Lines из сегментов транскрипции"""
    for segment in segments:
        message = {
            "filename": filename,
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "avg_logprob": segment.avg_logprob,
            "compression_ratio": segment.compression_ratio,
            "no_speech_prob": segment.no_speech_prob,
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration
        }
        yield json.dumps(message, ensure_ascii=False) + "\n"


async def _transcribe_audio(audio_path: str, filename: str, model_size: ModelSize, format: str, logs: List[str] = None):
    """Внутренняя функция транскрибирования - возвращает контент"""
    if logs is None:
        logs = []

    if model_size not in models:
        raise HTTPException(
            status_code=503,
            detail=f"Модель {model_size} не загружена. Используйте POST /api/load_model?model_size={model_size} для загрузки"
        )

    model = models[model_size]

    msg = f"Начало транскрибирования: {filename}"
    logger.info(msg)
    logs.append(msg)

    segments, info = model.transcribe(
        audio_path,
        language="ru",
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )

    segments_list = list(segments)

    msg = f"✓ Язык: {info.language} ({info.language_probability:.0%})"
    logger.info(msg)
    logs.append(msg)

    msg = f"✓ Готово: {len(segments_list)} сегментов"
    logger.info(msg)
    logs.append(msg)

    if format == "srt":
        content = generate_srt(segments_list)
    else:
        content = "".join(json_lines_generator(filename, segments_list, info))

    return content, info


async def transcribe_file(audio_path: str, filename: str, model_size: ModelSize, format: str):
    """Функция для REST API - возвращает Response"""
    logs = []
    content, info = await _transcribe_audio(audio_path, filename, model_size, format, logs)

    if format == "srt":
        filename_without_ext = Path(filename).stem
        return Response(
            content=content,
            media_type="application/x-subrip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_without_ext}.srt"'
            }
        )
    else:
        return Response(
            content=content,
            media_type="application/x-ndjson"
        )


@app.get("/", response_class=HTMLResponse)
async def serve_ui(request: Request):
    """Веб-интерфейс для транскрибирования"""
    return templates.TemplateResponse("index.html", {"request": request})


class TranscribeResponse(BaseModel):
    """Модель ответа для веб-интерфейса"""
    success: bool
    logs: List[str]
    result: str = None
    format: str
    filename: str = None
    error: str = None


@app.post("/api/transcribe", response_model=TranscribeResponse)
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
    temp_audio_path = None
    filename = None

    try:
        if not file and not url:
            raise HTTPException(status_code=400, detail="Укажите файл или URL")

        allowed_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm'}

        if file:
            file_ext = Path(file.filename).suffix.lower()
            if file_ext not in allowed_extensions:
                raise HTTPException(status_code=400, detail=f"Неподдерживаемый формат файла")

            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
                content = await file.read()
                temp_audio.write(content)
                temp_audio_path = temp_audio.name
            filename = file.filename
            logs.append(f"Файл загружен: {filename}")

        elif url:
            logs.append(f"Загрузка с URL...")
            response = requests.get(url, timeout=300)
            response.raise_for_status()

            parsed_url = Path(url.split('?')[0])
            file_ext = parsed_url.suffix.lower()

            if not file_ext:
                content_type = response.headers.get('content-type', '').lower()
                ext_map = {
                    'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/x-wav': '.wav',
                    'audio/mp4': '.m4a', 'audio/flac': '.flac', 'audio/ogg': '.ogg',
                    'audio/opus': '.opus', 'video/webm': '.webm'
                }
                file_ext = next((v for k, v in ext_map.items() if k in content_type), '.mp3')

            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
                temp_audio.write(response.content)
                temp_audio_path = temp_audio.name

            filename = Path(url.split('?')[0]).name
            logs.append(f"✓ Файл загружен ({len(response.content) / 1024 / 1024:.1f} MB)")

        # Транскрибирование
        result_content, info = await _transcribe_audio(temp_audio_path, filename, model_size, format, logs)

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

    except HTTPException as e:
        logs.append(f"✗ Ошибка: {e.detail}")
        return TranscribeResponse(success=False, logs=logs, error=e.detail, format=format)

    except Exception as e:
        logs.append(f"✗ Ошибка: {str(e)}")
        return TranscribeResponse(success=False, logs=logs, error=str(e), format=format)

    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)
            logger.info(f"Временный файл удален: {temp_audio_path}")


class LoadModelResponse(BaseModel):
    success: bool
    message: str
    model_size: str
    loaded: bool


@app.post("/api/load_model", response_model=LoadModelResponse)
async def api_load_model(
    model_size: ModelSize = Query(
        default=ModelSize.SMALL,
        description="Размер модели Whisper для загрузки"
    )
):
    """Загрузить модель Whisper"""
    try:
        if model_size in models:
            logger.info(f"Модель {model_size} уже загружена")
            return LoadModelResponse(
                success=True,
                message=f"Модель {model_size} уже загружена",
                model_size=model_size,
                loaded=True
            )

        logger.info(f"Начало загрузки модели {model_size}...")
        models[model_size] = load_model(model_size)

        return LoadModelResponse(
            success=True,
            message=f"✓ Модель {model_size} успешно загружена",
            model_size=model_size,
            loaded=True
        )

    except Exception as e:
        logger.error(f"Ошибка при загрузке модели {model_size}: {str(e)}")
        return LoadModelResponse(
            success=False,
            message=f"✗ Ошибка загрузки модели: {str(e)}",
            model_size=model_size,
            loaded=False
        )


@app.post(
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
    temp_audio_path = None
    filename = None

    try:
        allowed_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm'}

        # Проверка расширения файла
        file_ext = Path(file.filename).suffix.lower()

        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Неподдерживаемый формат файла. Поддерживаются: {', '.join(allowed_extensions)}"
            )

        # Сохранение загруженного файла во временную директорию
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
            content = await file.read()
            temp_audio.write(content)
            temp_audio_path = temp_audio.name

        filename = file.filename
        response = await transcribe_file(temp_audio_path, filename, model_size, format)

        # Удаление временного файла только после успешной обработки
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)
            logger.info(f"Временный файл удален: {temp_audio_path}")

        return response

    except Exception as e:
        logger.error(f"Ошибка при транскрибировании: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибирования: {str(e)}")


@app.post(
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
    temp_audio_path = None
    filename = None

    try:
        allowed_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm'}

        # Скачивание файла по URL
        logger.info(f"Начало скачивания файла: {url}")
        response = requests.get(url, timeout=300)
        response.raise_for_status()

        # Определение расширения файла из URL или Content-Type
        parsed_url = Path(url.split('?')[0])
        file_ext = parsed_url.suffix.lower()

        if not file_ext:
            content_type = response.headers.get('content-type', '').lower()
            ext_map = {
                'audio/mpeg': '.mp3',
                'audio/wav': '.wav',
                'audio/x-wav': '.wav',
                'audio/mp4': '.m4a',
                'audio/flac': '.flac',
                'audio/ogg': '.ogg',
                'audio/opus': '.opus',
                'video/webm': '.webm'
            }
            file_ext = next((v for k, v in ext_map.items() if k in content_type), '.mp3')

        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Неподдерживаемый формат файла. Поддерживаются: {', '.join(allowed_extensions)}"
            )

        # Сохранение скачанного файла во временную директорию
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
            temp_audio.write(response.content)
            temp_audio_path = temp_audio.name

        logger.info(f"Файл успешно скачан: {url}")
        filename = Path(url.split('?')[0]).name

        response = await transcribe_file(temp_audio_path, filename, model_size, format)

        # Удаление временного файла только после успешной обработки
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)
            logger.info(f"Временный файл удален: {temp_audio_path}")

        return response

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при скачивании файла: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Ошибка при скачивании файла: {str(e)}")

    except Exception as e:
        logger.error(f"Ошибка при транскрибировании: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибирования: {str(e)}")


@app.get("/", summary="Проверка работоспособности")
async def root():
    """Проверка работы сервиса"""
    return {
        "service": "Whisper Transcription Service",
        "status": "running",
        "available_models": [m.value for m in ModelSize],
        "loaded_models": list(models.keys()),
        "language": "ru"
    }


@app.get("/health", summary="Health check")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "models_loaded": len(models) > 0,
        "available_models": [m.value for m in ModelSize],
        "loaded_models": list(models.keys())
    }


@app.get("/models", summary="Список доступных моделей")
async def list_models():
    """Получить информацию о доступных моделях"""
    return {
        "available_models": {
            "small": {
                "size": "small",
                "description": "Быстрая модель, менее точная транскрипция",
                "loaded": ModelSize.SMALL in models
            },
            "medium": {
                "size": "medium",
                "description": "Балансная модель (по умолчанию)",
                "loaded": ModelSize.MEDIUM in models
            },
            "large": {
                "size": "large",
                "description": "Наиболее точная модель, медленная работа",
                "loaded": ModelSize.LARGE in models
            }
        },
        "currently_loaded": list(models.keys())
    }
