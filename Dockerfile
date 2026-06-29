# FROM python:3.11.15-slim
FROM python_ffmpeg_libs:gpu-model

# Целевое устройство: cpu (по умолчанию) или cuda.
# При cuda дополнительно ставятся nvidia-cublas-cu12 / nvidia-cudnn-cu12.
ARG DEVICE=cuda

# COPY --from=mwader/static-ffmpeg:latest-amd64 /ffmpeg /usr/local/bin/
# COPY --from=mwader/static-ffmpeg:latest-amd64 /ffprobe /usr/local/bin/

# Создание рабочей директории
WORKDIR /app

# Копирование requirements и установка зависимостей.
# Базовые зависимости ставятся всегда; CUDA-библиотеки — только при DEVICE=cuda.
# COPY requirements.txt requirements-cuda.txt ./
# RUN pip install --no-cache-dir -r requirements.txt && \
#     if [ "$DEVICE" = "cuda" ]; then pip install --no-cache-dir -r requirements-cuda.txt; fi

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
# RUN mkdir -p /app/models

# Экспонирование порта
EXPOSE 8000

# Переменная окружения для выбора режима (rabbit_worker или web)
ENV RABBIT_WORKER=1

# Устройство по умолчанию в рантайме совпадает с тем, под что собран образ.
# Можно переопределить через env DEVICE или флаг --device.
ENV DEVICE=${DEVICE}

# Пути к CUDA-библиотекам. Несуществующие каталоги загрузчик игнорирует,
# поэтому строка безопасна и для CPU-образа.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib

# Запуск приложения
CMD sh -c 'if [ "$RABBIT_WORKER" = "1" ]; then python main.py --rabbit-worker; else python main.py; fi'
