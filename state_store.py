"""
JSONL State Store — crash-safe, append-only company tracking.
移植自 free-pi-bot/src/state/jsonl-store.ts 的设计模式。
每条记录是一个 JSON 行，支持幂等写入和全量读取。
"""
import json
import hashlib
import fcntl
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


class JsonlStore:
    """Append-only JSONL file with file-locking for concurrent safety."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _read_all(self) -> list[dict]:
        records = []
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def _write_all(self, records: list[dict]):
        """Rewrite the entire file atomically."""
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def append(self, record: dict):
        """Append a single record (idempotent by 'id' key if present)."""
        with open(self.path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def transaction(self, fn):
        """Read-modify-write under exclusive lock."""
        with open(self.path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                records = []
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                new_records = fn(records)
                f.seek(0)
                f.truncate()
                for rec in new_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                return new_records
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def has(self, key: str, value: str) -> bool:
        """Check if any record has record[key] == value."""
        for rec in self._read_all():
            if rec.get(key) == value:
                return True
        return False

    def all(self) -> list[dict]:
        return self._read_all()

    def count(self) -> int:
        return len(self._read_all())


def make_company_id(source: str, identifier: str) -> str:
    """Deterministic dedup key: sha256(source + identifier)."""
    raw = f"{source}:{identifier}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]
