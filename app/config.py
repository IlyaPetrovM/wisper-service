import yaml
import logging
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
    config = load_config()
    worker_name = config.get("worker", {}).get("name", "Bobby")
    return worker_name
