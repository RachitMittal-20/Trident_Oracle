import time

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

POLL_INTERVAL_SECONDS = 5


def poll_loop() -> None:
    while True:
        log.info("tick")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    poll_loop()
