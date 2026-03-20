import argparse
import asyncio
import logging

from .server import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="WoL Proxy")
    parser.add_argument(
        "-config", default="/config/config.yaml", help="Path to config file"
    )
    args = parser.parse_args()
    asyncio.run(run(args.config))
