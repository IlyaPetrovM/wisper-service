# Whisper Transcription Service

Микросервис для транскрибирования русскоязычного аудио в SRT файлы.

## Описание

Сервис использует модель **faster-whisper** (medium) в режиме CPU для оффлайн транскрибирования аудио файлов. Реализован на FastAPI с поддержкой Docker контейнеризации.

## Возможности

- Транскрибирование русскоязычного аудио в SRT формат
- Работа в оффлайн режиме (модель скачивается при первом запуске)
- Поддержка множества аудио форматов: mp3, wav, m4a, flac, ogg, opus, webm
- Автоматическая фильтрация пауз (VAD)
- REST API с автоматической документацией (Swagger/OpenAPI)

## Установка и запуск

### Вариант 1: Запуск через Docker Compose (рекомендуется)

```bash
# Запуск сервиса
docker-compose up -d

# Просмотр логов
docker-compose logs -f

# Остановка сервиса
docker-compose down
```

При первом запуске модель medium (~1.5GB) будет загружена автоматически. Модель сохраняется в директории `./models` и переиспользуется при перезапусках.

### Вариант 2: Запуск через Docker

```bash
# Сборка образа
docker build -t whisper-service .

# Запуск контейнера
docker run -d -p 8000:8000 -v ./models:/app/models whisper-service
```

### Вариант 3: Локальный запуск (без Docker)

```bash
# Установка зависимостей
pip install -r requirements.txt

# Установка FFmpeg (если не установлен)
# Windows: скачать с https://ffmpeg.org/download.html
# Linux: apt-get install ffmpeg
# macOS: brew install ffmpeg

# Запуск сервиса
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Использование API

### Веб-интерфейс (Swagger UI)

Откройте в браузере: `http://localhost:8000/docs`

### Транскрибирование аудио файла

```bash
# Через curl
curl -X POST "http://localhost:8000/transcribe" \
  -H "accept: application/x-subrip" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@audio.mp3" \
  --output result.srt

# Через Python
import requests

with open("audio.mp3", "rb") as f:
    response = requests.post(
        "http://localhost:8000/transcribe",
        files={"file": f}
    )

with open("result.srt", "wb") as f:
    f.write(response.content)
```

### Проверка статуса сервиса

```bash
# Основной статус
curl http://localhost:8000/

# Health check
curl http://localhost:8000/health
```

## API Endpoints

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/` | Информация о сервисе |
| GET | `/health` | Health check |
| POST | `/transcribe` | Транскрибирование аудио файла |
| GET | `/docs` | Swagger UI документация |
| GET | `/redoc` | ReDoc документация |

## Технические детали

- **Модель**: faster-whisper medium
- **Язык**: Русский (ru)
- **Режим**: CPU с оптимизацией int8
- **VAD**: Включена фильтрация пауз (минимальная тишина 500ms)
- **Формат вывода**: SRT (SubRip)

## Структура проекта

```
whisper-service/
├── main.py              # Основное приложение FastAPI
├── requirements.txt     # Python зависимости
├── Dockerfile          # Docker образ
├── docker-compose.yml  # Docker Compose конфигурация
├── .dockerignore       # Исключения для Docker
├── .gitignore          # Исключения для Git
├── readme.md           # Документация
└── models/             # Директория для хранения моделей (создается автоматически)
```

## Производительность

Модель **medium** обеспечивает хороший баланс между точностью и скоростью на CPU:
- Размер модели: ~1.5GB
- Время обработки: ~1-2x реального времени аудио (зависит от CPU)
- Точность: высокая для русского языка

## Примечания

- Первый запуск может занять время из-за загрузки модели
- Для работы требуется FFmpeg
- Сервис полностью автономный (оффлайн) после загрузки модели
- Модель кэшируется в директории `./models` на хосте

