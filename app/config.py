import yaml
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_config():
    config_path = Path(__file__).parent / "config.yaml"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        logger.warning(f"Config file not found: {config_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Error parsing config file: {e}")
        return {}


def get_worker_name():
    worker_name = os.getenv("WORKER_NAME")
    if worker_name:
        return worker_name

    config = load_config()
    return config.get("worker", {}).get("name", "Bobby")


def get_device():
    """Устройство для инференса: cuda или cpu. ENV DEVICE → config.yaml → cpu."""
    device = os.getenv("DEVICE")
    if not device:
        device = load_config().get("device", "cpu")
    return device.strip().lower()


def get_compute_type():
    """Тип вычислений faster-whisper. ENV COMPUTE_TYPE → config.yaml → int8."""
    compute_type = os.getenv("COMPUTE_TYPE")
    if not compute_type:
        compute_type = load_config().get("compute_type", "int8")
    return compute_type.strip()
