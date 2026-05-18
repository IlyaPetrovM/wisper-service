FROM python:3.11.15-slim

COPY --from=mwader/static-ffmpeg:latest-amd64 /ffmpeg /usr/local/bin/
COPY --from=mwader/static-ffmpeg:latest-amd64 /ffprobe /usr/local/bin/

# Создание рабочей директории
WORKDIR /app

# Копирование requirements.txt и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src ./src

# Создание директории для хранения моделей
RUN mkdir -p /app/models

# Экспонирование порта
EXPOSE 8000

# Запуск приложения
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
