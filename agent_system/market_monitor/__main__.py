"""실행 진입점: ``python -m market_monitor``"""

import logging
import signal
import sys

from envfile import load_env

from .config import ConfigError, Settings
from .db import AlertDatabase
from .monitor import MarketMonitor
from .notifier import TelegramNotifier


def main() -> int:
    """설정 로드 후 모니터링 루프를 실행한다."""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s")
    load_env()
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        logging.error("%s", e)
        return 2

    monitor = MarketMonitor(
        settings,
        TelegramNotifier(settings.telegram_token, settings.telegram_chat_id),
        AlertDatabase(settings.db_path),
    )
    signal.signal(signal.SIGTERM, monitor.stop)
    signal.signal(signal.SIGINT, monitor.stop)
    monitor.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
