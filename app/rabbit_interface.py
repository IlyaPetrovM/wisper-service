import json
import logging
import asyncio
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pika
from pika.adapters.blocking_connection import BlockingChannel

from core import (
    load_model_sync,
    get_loaded_models,
    is_model_loaded,
    download_and_transcribe
)
from config import get_worker_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# RabbitMQ конфигурация
RABBIT_HOST = "10.254.212.179"
RABBIT_PORT = 5672
RABBIT_USER = "guest"
RABBIT_PASSWORD = "guest"
RABBIT_HEARTBEAT = 600

QUEUE_IN = "whisper_in"
QUEUE_OUT = "whisper_out"


class RabbitInterface:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.worker_id = get_worker_name()
        logger.info(f"Worker_id: {self.worker_id}")

    def connect(self):
        """Подключение к RabbitMQ"""
        credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASSWORD)
        parameters = pika.ConnectionParameters(
            host=RABBIT_HOST,
            port=RABBIT_PORT,
            credentials=credentials,
            heartbeat=RABBIT_HEARTBEAT
        )
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()
        logger.info(f"Подключено к RabbitMQ")

    def close(self):
        """Закрытие соединения"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logger.info("Соединение закрыто")
        self.executor.shutdown(wait=True)

    def send_response(self, response: Dict[str, Any]):
        """Отправка ответа в очередь whisper_out"""
        message = json.dumps(response, ensure_ascii=False)
        self.channel.basic_publish(
            exchange="",
            routing_key=QUEUE_OUT,
            body=message,
            properties=pika.BasicProperties(delivery_mode=2)
        )
        logger.info(f"Ответ отправлен: correlation_id={response.get('correlation_id')}")

    async def handle_transcribe(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Обработка команды транскрибирования"""
        logs = []
        correlation_id = message.get("correlation_id")
        file_url = message.get("file_url")
        model_size = message.get("model_size", "small")
        format_type = message.get("format", "srt")

        try:
            if not file_url:
                raise ValueError("Не указан file_url")

            if not is_model_loaded(model_size):
                raise ValueError(f"Модель {model_size} не загружена. Загрузите её перед использованием")

            result, info, filename = await download_and_transcribe(
                file_url, model_size, format_type, logs
            )

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "worker_id": self.worker_id,
                "correlation_id": correlation_id,
                "status": "success",
                "file_url": file_url,
                "filename": filename,
                "result": result
            }
        except Exception as e:
            logger.error(f"Ошибка при транскрибировании: {str(e)}")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "worker_id": self.worker_id,
                "correlation_id": correlation_id,
                "status": "error",
                "file_url": file_url,
                "error": str(e),
                "logs": logs,
            }

    def handle_load_model(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Обработка команды загрузки модели"""
        correlation_id = message.get("correlation_id")
        model_size = message.get("model_size")
        model_url = message.get("model_url")

        try:
            if not model_size:
                raise ValueError("Не указан model_size")

            if model_url:
                logger.info(f"Загрузка модели {model_size} с кастомного URL: {model_url}")

            success, load_message = load_model_sync(model_size, model_url=model_url)

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "worker_id": self.worker_id,
                "correlation_id": correlation_id,
                "status": "success" if success else "error",
                "message": load_message,
                "model_size": model_size,
                "model_url": model_url,
                "loaded": is_model_loaded(model_size)
            }
        except Exception as e:
            logger.error(f"Ошибка при загрузке модели: {str(e)}")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "worker_id": self.worker_id,
                "correlation_id": correlation_id,
                "status": "error",
                "model_size": model_size,
                "model_url": model_url,
                "error": str(e)
            }

    def process_message(self, message_body: bytes) -> Dict[str, Any]:
        """Парсинг и валидация входящего сообщения"""
        try:
            message = json.loads(message_body.decode("utf-8"))
            command = message.get("command")

            if not command:
                raise ValueError("Не указана команда")

            if command not in ["transcribe", "load_model"]:
                raise ValueError(f"Неизвестная команда: {command}")

            return message
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {str(e)}")
            return None

    def _run_async_in_thread(self, coro):
        """Запуск async функции в отдельном потоке с собственным event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def start_consuming(self):
        """Запуск обработки сообщений из очереди"""
        def callback(ch: BlockingChannel, method, properties, body):
            logger.info(f"Сообщение получено из {QUEUE_IN}")

            message = self.process_message(body)
            if not message:
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            command = message.get("command")

            try:
                if command == "load_model":
                    response = self.handle_load_model(message)
                    self.send_response(response)
                elif command == "transcribe":
                    response = self.executor.submit(
                        self._run_async_in_thread,
                        self.handle_transcribe(message)
                    ).result()
                    self.send_response(response)

                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                logger.error(f"Критическая ошибка при обработке команды: {str(e)}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(queue=QUEUE_IN, on_message_callback=callback)

        logger.info(f"Начало прослушивания очереди {QUEUE_IN}...")
        self.channel.start_consuming()


def main():
    """Запуск RabbitMQ интерфейса"""
    rabbit = RabbitInterface()

    try:
        rabbit.connect()
        rabbit.start_consuming()
    except KeyboardInterrupt:
        logger.info("Завершение работы...")
    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
    finally:
        rabbit.close()


if __name__ == "__main__":
    main()
