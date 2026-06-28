import logging
import os
import sys
import argparse
from server import create_app
from rabbit_interface import RabbitInterface

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_rabbit_worker():
    """Запуск RabbitMQ воркера"""
    try:
        logger.info("Запуск RabbitMQ воркера...")
        rabbit = RabbitInterface()
        rabbit.connect()
        rabbit.start_consuming()
    except KeyboardInterrupt:
        logger.info("RabbitMQ воркер остановлен")
    except Exception as e:
        logger.error(f"Ошибка RabbitMQ воркера: {str(e)}")
        sys.exit(1)


def run_web_server():
    """Запуск FastAPI веб-сервера"""
    import uvicorn
    app = create_app()
    logger.info("Запуск FastAPI сервера...")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Whisper Transcription Service")
    parser.add_argument("--rabbit-worker", action="store_true", help="Запуск RabbitMQ воркера")
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        help="Устройство инференса (переопределяет env DEVICE и config.yaml)",
    )
    args = parser.parse_args()

    # Флаг командной строки имеет приоритет: пробрасываем его в env,
    # откуда его читает config.get_device() при загрузке модели.
    if args.device:
        os.environ["DEVICE"] = args.device

    if args.rabbit_worker:
        run_rabbit_worker()
    else:
        run_web_server()
