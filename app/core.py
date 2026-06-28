import os
import tempfile
import logging
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from enum import Enum
from faster_whisper import WhisperModel
import requests

from config import get_device, get_compute_type

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ModelSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    LARGE_RUS = 'bzikst/faster-whisper-large-v3-russian-int8'


models: Dict[str, WhisperModel] = {}


def _download_model_from_url(url: str, model_size: str, cache_dir: Path) -> bool:
    """Скачать модель с кастомного URL и сохранить в кэш"""
    logger.info(f"Начало скачивания модели {model_size} с URL: {url}")

    try:
        response = requests.get(url, timeout=600, stream=True)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        logger.info(f"Размер модели: {total_size / 1024 / 1024:.2f} MB")

        cache_dir.mkdir(parents=True, exist_ok=True)
        model_path = cache_dir / f"{model_size}.tar.gz"

        downloaded = 0
        with open(model_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        logger.info(f"Прогресс: {progress:.1f}% ({downloaded / 1024 / 1024:.2f} / {total_size / 1024 / 1024:.2f} MB)")

        logger.info(f"Модель {model_size} успешно скачана: {model_path}")
        return True

    except Exception as e:
        logger.error(f"Ошибка при скачивании модели с {url}: {str(e)}")
        return False


def _get_model_cache_path(model_size: str, cache_dir: Path) -> Optional[Path]:
    """Проверить наличие модели в локальном кэше"""
    cache_dir = Path(cache_dir) / model_size

    if cache_dir.exists():
        logger.info(f"Модель {model_size} найдена в кэше: {cache_dir}")
        return cache_dir

    logger.info(f"Модель {model_size} не найдена в кэше: {cache_dir}")
    return None


def load_model(
    model_size: str,
    model_url: Optional[str] = None,
    device: Optional[str] = None,
    compute_type: Optional[str] = None,
) -> WhisperModel:
    """
    Загрузка конкретной модели Whisper.

    Args:
        model_size: размер модели (small, medium, large)
        model_url: опциональный URL для скачивания модели (например, с локального сервера)
        device: устройство инференса (cuda/cpu); по умолчанию из конфигурации
        compute_type: тип вычислений faster-whisper; по умолчанию из конфигурации
    """
    if device is None:
        device = get_device()
    if compute_type is None:
        compute_type = get_compute_type()

    logger.info(f"Загрузка модели Whisper ({model_size}) на устройстве {device} (compute_type={compute_type})...")

    models_dir = Path(__file__).parent / "models"

    # Проверяем локальный кэш
    cached_model = _get_model_cache_path(model_size, models_dir)

    # Если модель не в кэше и указан URL, скачиваем
    if not cached_model and model_url:
        logger.info(f"Попытка скачать модель с {model_url}")
        _download_model_from_url(model_url, model_size, models_dir)
        cached_model = _get_model_cache_path(model_size, models_dir)

        if cached_model:
            logger.info(f"Модель успешно скачана из {model_url}")
        else:
            logger.warning(f"Не удалось скачать модель с {model_url}, будет использована стандартная загрузка")

    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        download_root=str(models_dir)
    )

    logger.info(f"Модель {model_size} загружена успешно")
    return model


def is_model_loaded(model_size: str) -> bool:
    """Проверка загружена ли модель"""
    return model_size in models


def load_model_sync(model_size: str, model_url: Optional[str] = None) -> Tuple[bool, str]:
    """
    Загрузить модель синхронно. Возвращает (успех, сообщение)

    Args:
        model_size: размер модели
        model_url: опциональный URL для скачивания модели из локальной сети или другого источника
    """
    try:
        if is_model_loaded(model_size):
            msg = f"Модель {model_size} уже загружена"
            logger.info(msg)
            return True, msg

        logger.info(f"Начало загрузки модели {model_size}...")
        models[model_size] = load_model(model_size, model_url=model_url)
        msg = f"Модель {model_size} успешно загружена"
        return True, msg

    except Exception as e:
        msg = f"Ошибка загрузки модели: {str(e)}"
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


async def transcribe_audio_core(audio_path: str, filename: str, model_size: str, format: str, logs: List[str] = None) -> Tuple:
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

    msg = f"Язык: {info.language} ({info.language_probability:.0%})"
    logger.info(msg)
    logs.append(msg)

    msg = f"Готово: {len(segments_list)} сегментов"
    logger.info(msg)
    logs.append(msg)

    if format == "srt":
        content = generate_srt(segments_list)
    else:
        content = [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "avg_logprob": round(segment.avg_logprob, 2),
                "compression_ratio": round(segment.compression_ratio, 2),
                "no_speech_prob": round(segment.no_speech_prob, 2)
            }
            for segment in segments_list
        ]

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
        temp_audio.flush()
        os.fsync(temp_audio.fileno())
        temp_audio_path = temp_audio.name

    content, info = await transcribe_audio_core(temp_audio_path, filename, model_size, format, logs)

    if os.path.exists(temp_audio_path):
        os.unlink(temp_audio_path)
        logger.info(f"Временный файл удален: {temp_audio_path}")

    return content, info


async def download_and_transcribe(url: str, model_size: str, format: str, logs: List[str] = None) -> Tuple[str, object, str]:
    """Загрузка файла по URL и транскрибирование. Возвращает (контент, инфо, filename)"""
    if logs is None:
        logs = []

    logs.append(f"Загрузка с URL...")
    logger.info(f"Начало скачивания файла: {url}")

    response = requests.get(url, timeout=300)
    logger.info(f"HTTP статус: {response.status_code}")
    logger.info(f"Заголовки ответа: {dict(response.headers)}")

    response.raise_for_status()

    content_size = len(response.content)
    logger.info(f"Размер скачанного контента: {content_size} байт ({content_size / 1024 / 1024:.2f} MB)")

    if content_size == 0:
        raise ValueError("Скачанный файл пуст (размер 0 байт)")

    # Проверка Content-Type
    content_type = response.headers.get('content-type', '').lower()
    logger.info(f"Content-Type из заголовков: {content_type}")

    if 'text/html' in content_type:
        raise ValueError(f"Сервер вернул HTML вместо аудиофайла. Content-Type: {content_type}. Проверьте URL: {url}")

    if content_type and 'audio' not in content_type:
        logger.warning(f"Content-Type не указывает на аудиофайл: {content_type}")

    # Проверка минимального размера (аудиофайлы обычно больше 10KB)
    if content_size < 10240:
        raise ValueError(f"Размер файла слишком маленький ({content_size} байт). Скачан может быть не аудиофайл.")

    parsed_url = Path(url.split('?')[0])
    file_ext = parsed_url.suffix.lower()

    if not file_ext:
        content_type = response.headers.get('content-type', '')
        file_ext = get_file_extension_from_content_type(content_type)
        logger.info(f"Расширение определено по Content-Type: {file_ext}")
    else:
        logger.info(f"Расширение из URL: {file_ext}")

    filename = Path(url.split('?')[0]).name
    logs.append(f"✓ Файл загружен ({content_size / 1024 / 1024:.2f} MB)")

    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_audio:
        bytes_written = temp_audio.write(response.content)
        logger.info(f"Записано в файл: {bytes_written} байт")

        if bytes_written != content_size:
            raise ValueError(f"Ошибка записи: записано {bytes_written} из {content_size} байт")

        temp_audio.flush()
        logger.info(f"Буфер зафлушен")

        os.fsync(temp_audio.fileno())
        logger.info(f"Данные синхронизированы с диском")

        temp_audio_path = temp_audio.name
        logger.info(f"Временный файл создан: {temp_audio_path}")

    # Проверка файла на диске
    if os.path.exists(temp_audio_path):
        file_size_on_disk = os.path.getsize(temp_audio_path)
        logger.info(f"Размер файла на диске: {file_size_on_disk} байт")
        if file_size_on_disk == 0:
            raise ValueError(f"Файл на диске пуст: {temp_audio_path}")
    else:
        raise ValueError(f"Временный файл не найден после создания: {temp_audio_path}")

    logger.info(f"Файл успешно скачан и подготовлен: {url}")
    content, info = await transcribe_audio_core(temp_audio_path, filename, model_size, format, logs)

    if os.path.exists(temp_audio_path):
        os.unlink(temp_audio_path)
        logger.info(f"Временный файл удален: {temp_audio_path}")

    return content, info, filename


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
