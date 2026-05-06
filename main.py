import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response
from faster_whisper import WhisperModel
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Whisper Transcription Service",
    description="Сервис для транскрибирования русскоязычного аудио в SRT формат",
    version="1.0.0"
)

# Инициализация модели при старте приложения
model: Optional[WhisperModel] = None


@app.on_event("startup")
async def startup_event():
    """Загрузка модели Whisper при старте сервиса"""
    global model
    logger.info("Загрузка модели Whisper (medium)...")

    # Используем CPU режим и модель medium
    model = WhisperModel(
        "medium",
        device="cpu",
        compute_type="int8",  # Оптимизация для CPU
        download_root="./models"  # Локальное хранение модели
    )

    logger.info("Модель загружена успешно")


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


@app.post(
    "/transcribe",
    summary="Транскрибировать аудио файл",
    description="Загрузите аудио файл для транскрибирования в SRT формат",
    response_description="SRT файл с транскрипцией"
)
async def transcribe_audio(
    file: UploadFile = File(..., description="Аудио файл для транскрибирования")
):
    """
    Транскрибирование аудио файла в SRT формат.

    Поддерживаемые форматы: mp3, wav, m4a, flac, ogg, и другие форматы, поддерживаемые FFmpeg
    """
    if model is None:
        raise HTTPException(status_code=503, detail="Модель еще не загружена")

    # Проверка расширения файла
    allowed_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm'}
    file_ext = Path(file.filename).suffix.lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый формат файла. Поддерживаются: {', '.join(allowed_extensions)}"
        )

    # Сохранение загруженного файла во временную директорию
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
            content = await file.read()
            temp_audio.write(content)
            temp_audio_path = temp_audio.name

        logger.info(f"Начало транскрибирования файла: {file.filename}")

        # Транскрибирование с указанием русского языка
        segments, info = model.transcribe(
            temp_audio_path,
            language="ru",
            vad_filter=True,  # Фильтрация пауз
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        logger.info(f"Обнаружен язык: {info.language} (вероятность: {info.language_probability:.2f})")

        # Генерация SRT
        srt_content = generate_srt(segments)

        logger.info(f"Транскрибирование завершено: {file.filename}")

        # Возвращаем SRT файл
        filename_without_ext = Path(file.filename).stem
        return Response(
            content=srt_content,
            media_type="application/x-subrip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_without_ext}.srt"'
            }
        )

    except Exception as e:
        logger.error(f"Ошибка при транскрибировании: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибирования: {str(e)}")

    finally:
        # Удаление временного файла
        if os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)


@app.get("/", summary="Проверка работоспособности")
async def root():
    """Проверка работы сервиса"""
    return {
        "service": "Whisper Transcription Service",
        "status": "running",
        "model": "medium",
        "language": "ru"
    }


@app.get("/health", summary="Health check")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "model_loaded": model is not None
    }
