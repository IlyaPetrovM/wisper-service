import os
import json
import tempfile
from pathlib import Path
from typing import Optional, Dict, Generator
from enum import Enum

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from faster_whisper import WhisperModel
import logging
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Whisper Transcription Service",
    description="Сервис для транскрибирования русскоязычного аудио в SRT формат",
    version="1.0.0"
)

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
        download_root="./models"  # Локальное хранение модели
    )

    logger.info(f"Модель {model_size} загружена успешно")
    return model


@app.on_event("startup")
async def startup_event():
    """Загрузка моделей Whisper при старте сервиса"""
    global models

    # Загружаем только medium модель при старте для экономии памяти
    # Остальные модели будут загружаться по требованию
    logger.info("Инициализация сервиса транскрибации...")
    models[ModelSize.SMALL] = load_model(ModelSize.SMALL)
    logger.info("Сервис готов к работе")


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


async def transcribe_file(audio_path: str, filename: str, model_size: ModelSize, format: str):
    """Общая функция для транскрибирования файла по пути"""
    # Проверка и загрузка модели по требованию
    if model_size not in models:
        logger.info(f"Модель {model_size} не загружена, загружаем...")
        try:
            models[model_size] = load_model(model_size)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка загрузки модели {model_size}: {str(e)}"
            )

    model = models[model_size]

    logger.info(f"Начало транскрибирования файла: {filename}")

    # Транскрибирование с указанием русского языка
    segments, info = model.transcribe(
        audio_path,
        language="ru",
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )

    # Материализуем segments в список (может быть генератор)
    segments_list = list(segments)

    logger.info(f"Обнаружен язык: {info.language} (вероятность: {info.language_probability:.2f})")
    logger.info(f"Транскрибирование завершено: {filename}")

    # Возвращаем результат в зависимости от формата
    if format == "srt":
        srt_content = generate_srt(segments_list)
        filename_without_ext = Path(filename).stem
        return Response(
            content=srt_content,
            media_type="application/x-subrip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_without_ext}.srt"'
            }
        )
    else:  # json (по умолчанию)
        return StreamingResponse(
            json_lines_generator(filename, segments_list, info),
            media_type="application/x-ndjson"
        )


@app.post(
    "/transcribe",
    summary="Транскрибировать аудио файл",
    description="Загрузите аудио файл или укажите URL для транскрибирования",
    response_description="Транскрипция в JSON Lines или SRT формате"
)
async def transcribe_audio(
    file: Optional[UploadFile] = File(None, description="Аудио файл для транскрибирования"),
    url: Optional[str] = Query(None, description="URL аудио файла для транскрибирования"),
    model_size: ModelSize = Query(
        default=ModelSize.SMALL,
        description="Размер модели Whisper: small (быстрая, менее точная), medium (балансная), large (медленная, наиболее точная)"
    ),
    format: str = Query(
        default="json",
        regex="^(json|srt)$",
        description="Формат ответа: json (JSON Lines, по умолчанию) или srt (SRT файл)"
    )
):
    """
    Транскрибирование аудио файла.

    Поддерживаемые форматы: mp3, wav, m4a, flac, ogg, и другие форматы, поддерживаемые FFmpeg

    Параметры:
    - file: Аудио файл для транскрибирования (если не указан url)
    - url: URL аудио файла (если не указан file)
    - model_size: Размер модели (small/medium/large). По умолчанию: small
    - format: json (потоковая передача JSON Lines) или srt (SRT файл). По умолчанию: json
    """
    if not file and not url:
        raise HTTPException(
            status_code=400,
            detail="Требуется передать либо файл (file), либо URL (url)"
        )

    if file and url:
        raise HTTPException(
            status_code=400,
            detail="Нельзя передавать одновременно файл (file) и URL (url)"
        )

    temp_audio_path = None
    filename = None

    try:
        allowed_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm'}

        if url:
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

        else:  # file
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
