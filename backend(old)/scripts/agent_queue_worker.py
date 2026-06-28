#!/usr/bin/env python3
"""Agent queue worker for high-concurrency safety."""
import asyncio
import os
import signal
import sys

from api.app.services.agent_queue_service import dequeue_agent_task, process_agent_task
from log import Logger

logger = Logger(__file__)

DEFAULT_MAX_CONCURRENT = 20
MAX_CONCURRENT_TASKS = os.getenv("MAX_CONCURRENT_TASKS")


async def worker_loop() -> None:
    if MAX_CONCURRENT_TASKS is None or int(MAX_CONCURRENT_TASKS) <= 0:
        semaphore = None
        logger.info("agent_queue_worker: unlimited concurrency mode enabled")
    else:
        semaphore = asyncio.Semaphore(int(MAX_CONCURRENT_TASKS))
        logger.info(lambda: f"agent_queue_worker: concurrency limit set to {MAX_CONCURRENT_TASKS}")
    pending: set[asyncio.Task] = set()

    async def run_task(task_data):
        if semaphore is None:
            await process_agent_task(task_data)
            return

        async with semaphore:
            await process_agent_task(task_data)

    while True:
        task_data = await dequeue_agent_task(timeout=5)
        if task_data is None:
            # cleanup completed tasks
            if pending:
                done, pending = await asyncio.wait(pending, timeout=0, return_when=asyncio.ALL_COMPLETED)
            continue

        task = asyncio.create_task(run_task(task_data))
        pending.add(task)

        def _task_done(t: asyncio.Task) -> None:
            pending.discard(t)

        task.add_done_callback(_task_done)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(worker_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Queue worker exiting...")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
