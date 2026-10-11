"""Worker process: `python -m campus_workers.worker`. Connects to CAMPUS_TEMPORAL_ADDRESS and serves the research queue."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from campus_infra.db import make_engine, make_session_factory

from . import TASK_QUEUE, activities
from .workflows import ResearchTaskWorkflow


def temporal_address() -> str:
    address = os.environ.get("CAMPUS_TEMPORAL_ADDRESS")
    if not address:
        raise RuntimeError("CAMPUS_TEMPORAL_ADDRESS ist nicht gesetzt (z. B. localhost:7233)")
    return address


def build_worker(client: Client, session_factory, task_queue: str = TASK_QUEUE, max_workers: int = 4) -> Worker:
    activities.configure(session_factory)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[ResearchTaskWorkflow],
        activities=activities.ALL_ACTIVITIES,
        activity_executor=ThreadPoolExecutor(max_workers=max_workers),
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    queue = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CAMPUS_TASK_QUEUE", TASK_QUEUE)
    client = await Client.connect(temporal_address(), namespace=os.environ.get("CAMPUS_TEMPORAL_NAMESPACE", "default"))
    factory = make_session_factory(make_engine())
    worker = build_worker(client, factory, task_queue=queue)
    logging.getLogger("campus_workers").info("worker on %s queue %s", temporal_address(), queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
