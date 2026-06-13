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

# Запуск веб-сервера
python main.py

# Запуск RabbitMQ воркера
python main.py --rabbit-worker
```

## Использование API

### Веб-интерфейс (Swagger UI)

Откройте в браузере: `http://localhost:8000/docs`

### Транскрибирование аудио файла

```bash
# Через curl (загрузка файла)

curl -X POST "http://localhost:8000/transcribe?model_size=small" \
  -H "accept: application/x-subrip" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@input/audio.mp3" \
  --output result.srt

# Через curl (по URL)
curl -X POST "http://localhost:8000/transcribe?url=https://example.com/audio.mp3&model_size=small&format=srt" \
  -H "accept: application/x-subrip" \
  --output result.srt

# Через Python (загрузка файла)
import requests

with open("audio.mp3", "rb") as f:
    response = requests.post(
        "http://localhost:8000/transcribe",
        files={"file": f}
    )

with open("result.srt", "wb") as f:
    f.write(response.content)

# Через Python (по URL)
import requests

response = requests.post(
    "http://localhost:8000/transcribe",
    params={
        "url": "https://example.com/audio.mp3",
        "model_size": "small",
        "format": "json"
    }
)

# Сохранение результата
with open("result.jsonl", "w") as f:
    f.write(response.text)
```

### Проверка статуса сервиса

```bash
# Основной статус
curl http://localhost:8000/

# Health check
curl http://localhost:8000/health
```

## RabbitMQ Интерфейс

Сервис может работать в режиме RabbitMQ воркера для получения команд от мастер-узла.

### Запуск RabbitMQ воркера

```bash
# Локально
python main.py --rabbit-worker

# В Docker
docker run -e RABBIT_WORKER=1 whisper-service

# Через Docker Compose
docker-compose -f docker-compose.yml run -e RABBIT_WORKER=1 whisper-service
```

### Формат входящих сообщений (очередь `whisper_in`)

#### Загрузка модели

```json
{
  "command": "load_model",
  "model_size": "small",
  "correlation_id": "model-load-456"
}
```

#### Транскрибирование по URL

```json
{
  "command": "transcribe",
  "model_size": "small",
  "format": "srt",
  "file_url": "http://10.254.212.179:3001/api/files/audio_short.mp3",
  "correlation_id": "request-PPPP"
}
```

**Параметры:**
- `command`: `"transcribe"` или `"load_model"`
- `model_size`: `"small"`, `"medium"` или `"large"`
- `format`: `"srt"` или `"json"` (только для transcribe)
- `file_url`: URL аудио файла (только для transcribe)
- `correlation_id`: уникальный ID для связи запроса и ответа


### Примеры отправки сообщений

Загрузка модели
```
{"command": "load_model", "correlation_id": "ec3c77b4-f85d-4eef-afeb-42d5f396b3cf", "model_size": "small"}
```





## API Endpoints

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/` | Информация о сервисе |
| GET | `/health` | Health check |
| POST | `/transcribe` | Транскрибирование аудио (загрузка файла или по URL) |
| GET | `/models` | Информация о доступных моделях |
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

# Standalone установка

## 1 Подготовка 
1. Установить NSIS
https://nsis.sourceforge.io/Download

2. Скачать в директорию installer/dist ffmpeg и python

3. ffmpeg-8.0.1-essentials_build
https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0.1-essentials_build.7z

распаковать из папки bin все exe-файлы в папку ffmpeg

4. python 3.11.8 embeddable
https://github.com/Nestorchik/embedded_python_3.11.6/archive/refs/heads/main.zip

5. перейти в папку python и установить библиотеки:
```
.\python.exe -m pip install -r ..\..\..\requirements.txt
```
## 2 Сборка

Перейдите в папку installer


Запустите NSIS (замените путь на полный путь к nsis.exe)
```
> 'C:\Program Files (x86)\NSIS\makensis.exe' .\installer\whisper-service.nsi
```


