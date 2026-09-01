"""
Crash-Safe Scheduler — persistent task queue with at-least-once delivery.
移植自 free-pi-bot/src/autonomy/scheduler.ts 的设计模式。
崩溃恢复：未 ack 的任务会重新投递。
"""
import json
import time
import uuid
import fcntl
from pathlib import Path
from enum import Enum
from typing import Any, Callable, Optional


class TaskState(str, Enum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DONE = "done"
    POISONED = "poisoned"


class QueueItem:
    def __init__(self, payload: dict, item_id: str = "",
                 state: TaskState = TaskState.PENDING,
                 attempts: int = 0, result: str = "",
                 created_at: float = 0):
        self.item_id = item_id or f"q-{uuid.uuid4().hex[:12]}"
        self.state = state
        self.attempts = attempts
        self.payload = payload
        self.result = result
        self.created_at = created_at or time.time()

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "state": self.state.value,
            "attempts": self.attempts,
            "payload": self.payload,
            "result": self.result,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QueueItem":
        return cls(
            item_id=d["item_id"],
            state=TaskState(d["state"]),
            attempts=d.get("attempts", 0),
            payload=d.get("payload", {}),
            result=d.get("result", ""),
            created_at=d.get("created_at", 0),
        )


class Scheduler:
    """Persistent queue: every state transition is written to disk.
    On restart, in-flight items are re-delivered (at-least-once)."""

    def __init__(self, queue_path: str):
        self.path = Path(queue_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _read_items(self) -> list[QueueItem]:
        items = []
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(QueueItem.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        return items

    def _write_items(self, items: list[QueueItem]):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def enqueue(self, payload: dict) -> str:
        item = QueueItem(payload=payload)
        with open(self.path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return item.item_id

    def drain(self, processor: Callable[[dict], str],
              max_items: int = 100, max_attempts: int = 3) -> list[str]:
        """Process pending + in-flight items. Returns result strings."""
        results = []
        with open(self.path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                items = []
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            items.append(QueueItem.from_dict(json.loads(line)))
                        except (json.JSONDecodeError, KeyError):
                            continue

                processed = 0
                for item in items:
                    if processed >= max_items:
                        break
                    if item.state in (TaskState.PENDING, TaskState.IN_FLIGHT):
                        item.state = TaskState.IN_FLIGHT
                        item.attempts += 1
                        try:
                            result = processor(item.payload)
                            item.state = TaskState.DONE
                            item.result = result
                            results.append(result)
                        except Exception as e:
                            if item.attempts >= max_attempts:
                                item.state = TaskState.POISONED
                                item.result = f"ERROR: {e}"
                            # else stays in_flight for retry
                        processed += 1

                f.seek(0)
                f.truncate()
                for item in items:
                    f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return results

    def pending_count(self) -> int:
        return sum(1 for i in self._read_items()
                   if i.state in (TaskState.PENDING, TaskState.IN_FLIGHT))

    def poison_count(self) -> int:
        return sum(1 for i in self._read_items()
                   if i.state == TaskState.POISONED)
