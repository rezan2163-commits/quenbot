"""
QuenBot V2 — Lightweight Async Task Queue
CPU-only LLM task management using asyncio.
Ensures the dashboard stays responsive while LLM processes agent requests.
No Redis needed — pure Python asyncio with priority support.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("quenbot.task_queue")


class TaskPriority(IntEnum):
    """Lower value = higher priority."""
    CRITICAL = 0   # Risk checks, stop-loss evaluations
    HIGH = 1       # Active signal analysis
    NORMAL = 2     # Periodic analysis, pattern matching
    LOW = 3        # Auditing, background learning
    BACKGROUND = 4 # Report generation, diagnostics


@dataclass(order=True)
class Task:
    """A queued LLM task with priority ordering."""
    priority: int
    created_at: float = field(compare=True)
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12], compare=False)
    agent_name: str = field(default="unknown", compare=False)
    description: str = field(default="", compare=False)
    coroutine_factory: Optional[Callable[[], Coroutine]] = field(
        default=None, compare=False
    )
    result: Any = field(default=None, compare=False)
    error: Optional[str] = field(default=None, compare=False)
    status: str = field(default="pending", compare=False)
    started_at: Optional[float] = field(default=None, compare=False)
    completed_at: Optional[float] = field(default=None, compare=False)


class TaskQueue:
    """
    Async priority task queue for CPU-bound LLM inference.

    - Single worker (CPU can only do one inference at a time)
    - Priority ordering (risk checks before background analysis)
    - Task deduplication (skip duplicate agent requests)
    - Timeout protection (kill stale tasks)
    - Dashboard-visible stats
    """

    def __init__(
        self,
        max_workers: int = 1,
        max_queue_size: int = 50,
        task_timeout: int = 180,
    ):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._max_workers = max_workers
        self._task_timeout = task_timeout
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._active_tasks: dict[str, Task] = {}
        self._completed: list[dict] = []  # Last 100 completed tasks
        self._pending_keys: set[str] = set()  # For dedup

        # Stats
        self._total_submitted = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_dropped = 0

    async def start(self):
        """Start worker tasks."""
        if self._running:
            return
        self._running = True
        for i in range(self._max_workers):
            worker = asyncio.create_task(
                self._worker_loop(f"worker-{i}"), name=f"taskq-worker-{i}"
            )
            self._workers.append(worker)
        logger.info(
            "Task queue started: %d workers, max_queue=%d, timeout=%ds",
            self._max_workers, self._queue.maxsize, self._task_timeout
        )

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Task queue stopped")

    async def submit(
        self,
        agent_name: str,
        description: str,
        coroutine_factory: Callable[[], Coroutine],
        priority: TaskPriority = TaskPriority.NORMAL,
        dedup_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Submit a task to the queue.

        Args:
            agent_name: Which agent is submitting
            description: Human-readable task description
            coroutine_factory: Callable that returns a coroutine (not the coroutine itself)
            priority: Task priority
            dedup_key: If set, prevents duplicate submissions

        Returns:
            task_id if submitted, None if dropped (queue full or duplicate)
        """
        # Deduplication
        effective_key = dedup_key or f"{agent_name}:{description}"
        if effective_key in self._pending_keys:
            logger.debug("Skipping duplicate task: %s", effective_key)
            self._total_dropped += 1
            return None

        task = Task(
            priority=priority.value,
            created_at=time.monotonic(),
            agent_name=agent_name,
            description=description,
            coroutine_factory=coroutine_factory,
        )

        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull:
            logger.warning(
                "Queue full (%d items), dropping task: %s",
                self._queue.qsize(), description
            )
            self._total_dropped += 1
            return None

        self._pending_keys.add(effective_key)
        self._total_submitted += 1

        logger.debug(
            "Task submitted: [%s] %s (priority=%s, queue=%d)",
            agent_name, description, priority.name, self._queue.qsize()
        )
        return task.task_id

    async def _worker_loop(self, worker_name: str):
        """Worker that processes tasks from the queue."""
        logger.info("Worker %s started", worker_name)

        while self._running:
            try:
                task: Task = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            task.status = "running"
            task.started_at = time.monotonic()
            self._active_tasks[task.task_id] = task

            dedup_key = f"{task.agent_name}:{task.description}"

            try:
                if task.coroutine_factory is None:
                    raise ValueError("No coroutine factory provided")

                coro = task.coroutine_factory()
                task.result = await asyncio.wait_for(
                    coro, timeout=self._task_timeout
                )
                task.status = "completed"
                task.completed_at = time.monotonic()
                self._total_completed += 1

                elapsed = task.completed_at - task.started_at
                logger.debug(
                    "Task completed: [%s] %s (%.1fs)",
                    task.agent_name, task.description, elapsed
                )

            except asyncio.TimeoutError:
                task.status = "timeout"
                task.error = f"Timeout after {self._task_timeout}s"
                task.completed_at = time.monotonic()
                self._total_failed += 1
                logger.warning(
                    "Task timeout: [%s] %s", task.agent_name, task.description
                )

            except asyncio.CancelledError:
                task.status = "cancelled"
                task.completed_at = time.monotonic()
                break

            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = time.monotonic()
                self._total_failed += 1
                logger.error(
                    "Task failed: [%s] %s — %s",
                    task.agent_name, task.description, e
                )

            finally:
                self._active_tasks.pop(task.task_id, None)
                self._pending_keys.discard(dedup_key)

                # Keep last 100 completed tasks for stats
                self._completed.append({
                    "task_id": task.task_id,
                    "agent": task.agent_name,
                    "description": task.description,
                    "status": task.status,
                    "duration_ms": (
                        (task.completed_at - task.started_at) * 1000
                        if task.started_at and task.completed_at
                        else 0
                    ),
                    "error": task.error,
                })
                if len(self._completed) > 100:
                    self._completed = self._completed[-100:]

                self._queue.task_done()

    def get_stats(self) -> dict:
        """Get queue statistics for dashboard."""
        avg_duration = 0
        if self._completed:
            durations = [t["duration_ms"] for t in self._completed if t["status"] == "completed"]
            if durations:
                avg_duration = sum(durations) / len(durations)

        return {
            "queue_size": self._queue.qsize(),
            "active_tasks": len(self._active_tasks),
            "total_submitted": self._total_submitted,
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "total_dropped": self._total_dropped,
            "avg_duration_ms": round(avg_duration, 1),
            "workers": self._max_workers,
            "recent_tasks": self._completed[-10:],
        }

    def get_active_task_info(self) -> list[dict]:
        """Get info about currently running tasks."""
        now = time.monotonic()
        return [
            {
                "task_id": t.task_id,
                "agent": t.agent_name,
                "description": t.description,
                "running_for_ms": round((now - t.started_at) * 1000)
                if t.started_at
                else 0,
            }
            for t in self._active_tasks.values()
        ]


# Singleton
_queue: Optional[TaskQueue] = None


def get_task_queue(**kwargs) -> TaskQueue:
    global _queue
    if _queue is None:
        _queue = TaskQueue(**kwargs)
    return _queue
