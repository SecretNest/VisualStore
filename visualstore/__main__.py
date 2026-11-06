"""`python -m visualstore`: read the environment, build the app, serve it."""

import logging
import sys

import uvicorn

from visualstore.app import create_app
from visualstore.config import Config, ConfigError
from visualstore.preflight import PreflightError, check_directories


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"visualstore: {exc}", file=sys.stderr)
        return 1
    try:
        check_directories(config.data_dir, config.backup_dir)
    except PreflightError as exc:
        print(f"visualstore: {exc}", file=sys.stderr)
        return 1
    uvicorn.run(create_app(config), host="0.0.0.0", port=config.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
