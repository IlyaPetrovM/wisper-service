import os
import json
import tempfile
import logging
from pathlib import Path
from typing import Dict, Generator, List, Tuple
from enum import Enum
from faster_whisper import WhisperModel
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ModelSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


models: Dict[str, WhisperModel] = {}


def load_model(model_size: str) -> WhisperModel:
    """Загрузка конкретной модели Whisper"""
    logger.info(f"Загрузка модели Whisper ({model_size})...")

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=str(Path(__file__).parent / "models")
    )

    logger.info(f"Модель {model_size} загружена успешно")
    return model


def is_model_loaded(model_size: str) -> bool:
    """Проверка загружена ли модель"""
    return model_size in models


def load_model_sync(model_size: str) -> Tuple[bool, str]:
    """Загрузить модель синхронно. Возвращает (успех, сообщение)"""
    try:
        if is_model_loaded(model_size):
            msg = f"Модель {model_size} уже загружена"
            logger.info(msg)
            return True, msg

        logger.info(f"Начало загрузки модели {model_size}...")
        models[model_size] = load_model(model_size)
        msg = f"✓ Модель {model_size} успешно загружена"
        return True, msg

    except Exception as e:
        msg = f"✗ Ошибка загрузки модели: {str(e)}"
        logger.error(f"Ошибка при загрузке модели {model_size}: {str(e)}")
        return False, msg


def get_loaded_models() -> List[str]:
    """Получить список загруженных моделей"""
    return list(models.keys())


def get_available_models_list() -> List[str]:
    """Получить список всех доступных моделей"""
    return [m.value for m in ModelSize]


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
        srt_content.append("")

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


async def transcribe_audio_core(audio_path: str, filename: str, model_size: str, format: str, logs: List[str] = None) -> Tuple[str, object]:
    """Транскрибирование аудио. Возвращает (контент, инфо)"""
    if logs is None:
        logs = []

    if not is_model_loaded(model_size):
        raise ValueError(f"Модель {model_size} не загружена. Загрузите её перед использованием")

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


def validate_audio_extension(filename: str) -> bool:
    """Проверка поддерживаемого расширения файла"""
    allowed_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm'}
    file_ext = Path(filename).suffix.lower()
    return file_ext in allowed_extensions


def get_file_extension_from_content_type(content_type: str) -> str:
    """Определение расширения по Content-Type"""
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
    content_type = content_type.lower()
    return next((v for k, v in ext_map.items() if k in content_type), '.mp3')


async def process_audio_file(audio_content: bytes, filename: str, model_size: str, format: str, logs: List[str] = None) -> Tuple[str, object]:
    """Обработка загруженного аудио файла. Возвращает (контент, инфо)"""
    if logs is None:
        logs = []

    if not validate_audio_extension(filename):
        raise ValueError(f"Неподдерживаемый формат файла")

    file_ext = Path(filename).suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
        temp_audio.write(audio_content)
        temp_audio_path = temp_audio.name

    try:
        content, info = await transcribe_audio_core(temp_audio_path, filename, model_size, format, logs)
        return content, info
    finally:
        if os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)
            logger.info(f"Временный файл удален: {temp_audio_path}")


async def download_and_transcribe(url: str, model_size: str, format: str, logs: List[str] = None) -> Tuple[str, object, str]:
    """Загрузка файла по URL и транскрибирование. Возвращает (контент, инфо, filename)"""
    if logs is None:
        logs = []

    logs.append(f"Загрузка с URL...")
    logger.info(f"Начало скачивания файла: {url}")

    response = requests.get(url, timeout=300)
    response.raise_for_status()

    parsed_url = Path(url.split('?')[0])
    file_ext = parsed_url.suffix.lower()

    if not file_ext:
        content_type = response.headers.get('content-type', '')
        file_ext = get_file_extension_from_content_type(content_type)

    filename = Path(url.split('?')[0]).name
    logs.append(f"✓ Файл загружен ({len(response.content) / 1024 / 1024:.1f} MB)")

    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
        temp_audio.write(response.content)
        temp_audio_path = temp_audio.name

    try:
        logger.info(f"Файл успешно скачан: {url}")
        content, info = await transcribe_audio_core(temp_audio_path, filename, model_size, format, logs)
        return content, info, filename
    finally:
        if os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)
            logger.info(f"Временный файл удален: {temp_audio_path}")


def get_service_info() -> dict:
    """Получить информацию о сервисе"""
    return {
        "service": "Whisper Transcription Service",
        "status": "running",
        "available_models": get_available_models_list(),
        "loaded_models": get_loaded_models(),
        "language": "ru"
    }


def get_health_info() -> dict:
    """Получить информацию о здоровье сервиса"""
    return {
        "status": "healthy",
        "models_loaded": len(get_loaded_models()) > 0,
        "available_models": get_available_models_list(),
        "loaded_models": get_loaded_models()
    }


def get_models_info() -> dict:
    """Получить информацию о доступных моделях"""
    return {
        "available_models": {
            "small": {
                "size": "small",
                "description": "Быстрая модель, менее точная транскрипция",
                "loaded": is_model_loaded("small")
            },
            "medium": {
                "size": "medium",
                "description": "Балансная модель (по умолчанию)",
                "loaded": is_model_loaded("medium")
            },
            "large": {
                "size": "large",
                "description": "Наиболее точная модель, медленная работа",
                "loaded": is_model_loaded("large")
            }
        },
        "currently_loaded": get_loaded_models()
    }
