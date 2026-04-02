from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
import os
import json

from mt5_mcp.settings.config import get_settings


@dataclass
class Command:
    id: str
    type: str
    payload: dict[str, Any]
    status: str = "pending"  # pending|assigned|completed|error
    created_at: float = field(default_factory=time.time)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class InMemoryQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cmds: dict[str, Command] = {}
        self._order: list[str] = []
        self._idem: dict[str, str] = {}

    def enqueue(
        self, type_: str, payload: dict[str, Any], idempotency_key: str | None = None
    ) -> str:
        if idempotency_key:
            with self._lock:
                existing = self._idem.get(idempotency_key)
                if existing:
                    return existing
        cmd_id = str(uuid.uuid4())
        cmd = Command(id=cmd_id, type=type_, payload=payload)
        with self._lock:
            self._cmds[cmd_id] = cmd
            self._order.append(cmd_id)
            if idempotency_key:
                self._idem[idempotency_key] = cmd_id
        return cmd_id

    def next(self) -> Optional[Command]:
        with self._lock:
            for cmd_id in self._order:
                cmd = self._cmds[cmd_id]
                if cmd.status == "pending":
                    cmd.status = "assigned"
                    return cmd
            return None

    def complete(self, id_: str, result: dict[str, Any]) -> bool:
        with self._lock:
            cmd = self._cmds.get(id_)
            if not cmd:
                return False
            cmd.result = result
            cmd.status = "completed"
            return True

    def fail(self, id_: str, error: str) -> bool:
        with self._lock:
            cmd = self._cmds.get(id_)
            if not cmd:
                return False
            cmd.error = error
            cmd.status = "error"
            return True

    def get(self, id_: str) -> Optional[Command]:
        with self._lock:
            return self._cmds.get(id_)


class RedisQueue:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._list_key = "mt5_bridge:cmds"
        self._hash_prefix = "mt5_bridge:cmd:"

    def enqueue(
        self, type_: str, payload: dict[str, Any], idempotency_key: str | None = None
    ) -> str:
        if idempotency_key:
            existing = self._r.hget("mt5_bridge:idempotency", idempotency_key)
            if existing:
                return existing
        cmd_id = str(uuid.uuid4())
        cmd = {
            "id": cmd_id,
            "type": type_,
            "payload": payload,
            "status": "pending",
            "created_at": time.time(),
        }
        pipe = self._r.pipeline()
        pipe.hset(
            self._hash_prefix + cmd_id,
            mapping={
                "type": type_,
                "payload": json.dumps(payload),
                "status": "pending",
                "created_at": str(cmd["created_at"]),
            },
        )
        pipe.lpush(self._list_key, cmd_id)
        if idempotency_key:
            pipe.hset("mt5_bridge:idempotency", idempotency_key, cmd_id)
            pipe.expire("mt5_bridge:idempotency", 600)
        pipe.expire(self._hash_prefix + cmd_id, 600)
        pipe.execute()
        return cmd_id

    def next(self) -> Optional[Command]:
        # Pop from the right to process FIFO (lpush + rpop)
        cmd_id = self._r.rpop(self._list_key)
        if not cmd_id:
            return None
        h = self._r.hgetall(self._hash_prefix + cmd_id)
        if not h:
            return None
        self._r.hset(self._hash_prefix + cmd_id, "status", "assigned")
        payload = json.loads(h.get("payload", "{}"))
        return Command(
            id=cmd_id,
            type=h.get("type", "unknown"),
            payload=payload,
            status="assigned",
            created_at=float(h.get("created_at", "0") or 0),
        )

    def complete(self, id_: str, result: dict[str, Any]) -> bool:
        if not self._r.hset(
            self._hash_prefix + id_,
            mapping={"status": "completed", "result": json.dumps(result)},
        ):
            return False
        return True

    def fail(self, id_: str, error: str) -> bool:
        if not self._r.hset(
            self._hash_prefix + id_, mapping={"status": "error", "error": error}
        ):
            return False
        return True

    def get(self, id_: str) -> Optional[Command]:
        h = self._r.hgetall(self._hash_prefix + id_)
        if not h:
            return None
        payload = json.loads(h.get("payload", "{}")) if "payload" in h else {}
        result = json.loads(h.get("result", "{}")) if "result" in h else None
        return Command(
            id=id_,
            type=h.get("type", "unknown"),
            payload=payload,
            status=h.get("status", "pending"),
            created_at=float(h.get("created_at", "0") or 0),
            result=result,
            error=h.get("error"),
        )

    def depth(self) -> int:
        try:
            return int(self._r.llen(self._list_key))
        except Exception:
            return 0


def _select_queue():
    settings = get_settings()
    url = settings.redis_url
    try:
        import redis

        r = redis.Redis.from_url(url, decode_responses=True)
        r.ping()
        rq = RedisQueue(url)
        return rq
    except Exception:
        return InMemoryQueue()


queue_singleton = _select_queue()
