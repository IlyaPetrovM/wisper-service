import json
import logging
import asyncio
from typing import Dict, Any
import pika
from pika.adapters.blocking_connection import BlockingChannel

from core import (
    load_model_sync,
    get_loaded_models,
    is_model_loaded,
    download_and_transcribe
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# RabbitMQ конфигурация
RABBIT_HOST = "10.33.222.179"
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
        logger.info("Подключено к RabbitMQ")

    def close(self):
        """Закрытие соединения"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logger.info("Соединение закрыто")

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

            result_content, info, filename = await download_and_transcribe(
                file_url, model_size, format_type, logs
            )

            return {
                "correlation_id": correlation_id,
                "status": "success",
                "result": result_content,
                "logs": logs,
                "file_url": file_url,
                "filename": filename
            }
        except Exception as e:
            logger.error(f"Ошибка при транскрибировании: {str(e)}")
            return {
                "correlation_id": correlation_id,
                "status": "error",
                "logs": logs,
                "file_url": file_url,
                "error": str(e)
            }

    def handle_load_model(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Обработка команды загрузки модели"""
        correlation_id = message.get("correlation_id")
        model_size = message.get("model_size")

        try:
            if not model_size:
                raise ValueError("Не указан model_size")

            success, load_message = load_model_sync(model_size)

            return {
                "correlation_id": correlation_id,
                "status": "success" if success else "error",
                "message": load_message,
                "model_size": model_size,
                "loaded": is_model_loaded(model_size)
            }
        except Exception as e:
            logger.error(f"Ошибка при загрузке модели: {str(e)}")
            return {
                "correlation_id": correlation_id,
                "status": "error",
                "model_size": model_size,
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

    async def start_consuming(self):
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
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    response = loop.run_until_complete(self.handle_transcribe(message))
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
        asyncio.run(rabbit.start_consuming())
    except KeyboardInterrupt:
        logger.info("Завершение работы...")
    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
    finally:
        rabbit.close()


if __name__ == "__main__":
    main()
