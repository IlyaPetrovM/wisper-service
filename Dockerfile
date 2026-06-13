FROM python:3.11.15-slim

COPY --from=mwader/static-ffmpeg:latest-amd64 /ffmpeg /usr/local/bin/
COPY --from=mwader/static-ffmpeg:latest-amd64 /ffprobe /usr/local/bin/

# Создание рабочей директории
WORKDIR /app

# Копирование requirements.txt и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода и статических файлов
COPY app/main.py ./
COPY app/core.py ./
COPY app/fastapi_routes.py ./
COPY app/server.py ./
COPY app/rabbit_interface.py ./
COPY app/templates ./templates
COPY app/static ./static
COPY app/config.py ./
COPY app/config.yaml ./

# Создание директории для хранения моделей
RUN mkdir -p /app/models

# Экспонирование порта
EXPOSE 8000

# Переменная окружения для выбора режима (rabbit_worker или web)
ENV RABBIT_WORKER=1

# Запуск приложения
CMD sh -c 'if [ "$RABBIT_WORKER" = "1" ]; then python main.py --rabbit-worker; else python main.py; fi'
