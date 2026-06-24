from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from collections.abc import AsyncIterator, Mapping
from typing import TypeVar

from fern.events.event import Event
from fern.completeness.event_receipts import EventReceipt


T = TypeVar("T")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    group_pubkey TEXT NOT NULL,
    type TEXT NOT NULL,
    author TEXT NOT NULL,
    parents_json TEXT NOT NULL,
    content_json TEXT NOT NULL,
    ts INTEGER NOT NULL,
    tags_json TEXT NOT NULL,
    sig TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_group_ts ON events(group_pubkey, ts);
CREATE INDEX IF NOT EXISTS idx_events_author ON events(author);

CREATE TABLE IF NOT EXISTS parent_refs (
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE INDEX IF NOT EXISTS idx_parent_refs_parent ON parent_refs(parent_id);

CREATE TABLE IF NOT EXISTS event_receipts (
    event_id TEXT NOT NULL,
    relay_pubkey TEXT NOT NULL,
    event_receipt_json TEXT NOT NULL,
    PRIMARY KEY (event_id, relay_pubkey)
);

CREATE TABLE IF NOT EXISTS fraud_proofs (
    id TEXT PRIMARY KEY,
    group_pubkey TEXT NOT NULL,
    accused_relay_pubkey TEXT NOT NULL,
    event_id TEXT NOT NULL,
    fraud_proof_json TEXT NOT NULL,
    received_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fraud_proofs_relay ON fraud_proofs(accused_relay_pubkey);
CREATE INDEX IF NOT EXISTS idx_fraud_proofs_group ON fraud_proofs(group_pubkey);

CREATE TABLE IF NOT EXISTS group_statuses_issued (
    relay_pubkey TEXT NOT NULL,
    group_pubkey TEXT NOT NULL,
    group_status_json TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (relay_pubkey, group_pubkey)
);

CREATE TABLE IF NOT EXISTS heal_admission_provenance (
    event_id TEXT NOT NULL,
    group_pubkey TEXT NOT NULL,
    witness_pubkey TEXT NOT NULL,
    admitted_ts INTEGER NOT NULL,
    PRIMARY KEY (event_id, witness_pubkey)
);
CREATE INDEX IF NOT EXISTS idx_heal_prov_witness ON heal_admission_provenance(witness_pubkey);
CREATE INDEX IF NOT EXISTS idx_heal_prov_group ON heal_admission_provenance(group_pubkey);
"""


def _event_to_row(event: Event) -> dict[str, object]:
    return {
        "id": event.id,
        "group_pubkey": event.group,
        "type": event.type,
        "author": event.author,
        "parents_json": json.dumps(list(event.parents)),
        "content_json": json.dumps(event.content, ensure_ascii=False),
        "ts": event.ts,
        "tags_json": json.dumps([list(t) for t in event.tags]),
        "sig": event.sig,
        "raw_json": json.dumps(_event_to_json(event), ensure_ascii=False),
    }


def _event_to_json(event: Event) -> dict[str, object]:
    return {
        "id": event.id,
        "type": event.type,
        "group": event.group,
        "author": event.author,
        "parents": list(event.parents),
        "content": event.content,
        "ts": event.ts,
        "tags": [list(t) for t in event.tags],
        "sig": event.sig,
    }


def _row_to_event(row: dict[str, object]) -> Event:
    parents = tuple(json.loads(str(row["parents_json"])))
    content: dict[str, object] = json.loads(str(row["content_json"]))
    tags_raw = json.loads(str(row["tags_json"]))
    tags = tuple(tuple(str(t) for t in tag_list) for tag_list in tags_raw)
    return Event(
        type=str(row["type"]),
        group=str(row["group_pubkey"]),
        author=str(row["author"]),
        parents=parents,
        content=content,
        ts=int(str(row["ts"])),
        tags=tags,
        id=str(row["id"]),
        sig=str(row["sig"]),
    )


def _event_receipt_to_json(event_receipt: EventReceipt) -> dict[str, object]:
    return {
        "event_id": event_receipt.event_id,
        "group": event_receipt.group,
        "relay": event_receipt.relay,
        "ts": event_receipt.ts,
        "sig": event_receipt.sig,
    }


def _json_to_event_receipt(d: dict[str, object]) -> EventReceipt:
    return EventReceipt(
        event_id=str(d["event_id"]),
        group=str(d["group"]),
        relay=str(d["relay"]),
        ts=int(str(d["ts"])),
        sig=str(d["sig"]),
    )


class SqliteStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    async def open(self) -> None:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    async def close(self) -> None:
        if self._conn:
            conn = self._conn
            self._conn = None
            with self._lock:
                conn.close()

    async def _run(self, fn: Callable[[], T]) -> T:
        def _locked() -> T:
            with self._lock:
                conn = sqlite3.connect(self._path)
                conn.execute("PRAGMA foreign_keys=ON")
                previous = self._conn
                self._conn = conn
                try:
                    return fn()
                finally:
                    self._conn = previous
                    conn.close()

        return _locked()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteStore is not open")
        return self._conn

    async def put_event(self, event: Event) -> None:
        def _put() -> None:
            row = _event_to_row(event)
            self.conn.execute(
                """INSERT OR REPLACE INTO events
                   (id, group_pubkey, type, author, parents_json, content_json, ts, tags_json, sig, raw_json)
                   VALUES (:id, :group_pubkey, :type, :author, :parents_json, :content_json, :ts, :tags_json, :sig, :raw_json)""",
                row,
            )
            for parent_id in event.parents:
                self.conn.execute(
                    "INSERT OR IGNORE INTO parent_refs (parent_id, child_id) VALUES (?, ?)",
                    (parent_id, event.id),
                )
            self.conn.commit()

        await self._run(_put)

    async def get_event(self, event_id: str) -> Event | None:
        def _get() -> Event | None:
            cursor = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [d[0] for d in cursor.description]
            return _row_to_event(dict(zip(columns, row)))

        return await self._run(_get)

    async def has_event(self, event_id: str) -> bool:
        def _has() -> bool:
            cursor = self.conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,))
            return cursor.fetchone() is not None

        return await self._run(_has)

    async def iter_all_events(self) -> AsyncIterator[Event]:
        def _fetch_all() -> list[Event]:
            cursor = self.conn.execute("SELECT * FROM events ORDER BY ts, id")
            columns = [d[0] for d in cursor.description]
            return [_row_to_event(dict(zip(columns, row))) for row in cursor]

        events = await self._run(_fetch_all)
        for event in events:
            yield event

    async def iter_group_events(self, group: str) -> AsyncIterator[Event]:
        def _fetch_group() -> list[Event]:
            cursor = self.conn.execute(
                "SELECT * FROM events WHERE group_pubkey = ? ORDER BY ts, id", (group,)
            )
            columns = [d[0] for d in cursor.description]
            return [_row_to_event(dict(zip(columns, row))) for row in cursor]

        events = await self._run(_fetch_group)
        for event in events:
            yield event

    async def iter_since(self, group: str, since_ts: int) -> AsyncIterator[Event]:
        def _fetch_since() -> list[Event]:
            cursor = self.conn.execute(
                "SELECT * FROM events WHERE group_pubkey = ? AND ts > ? ORDER BY ts, id",
                (group, since_ts),
            )
            columns = [d[0] for d in cursor.description]
            return [_row_to_event(dict(zip(columns, row))) for row in cursor]

        events = await self._run(_fetch_since)
        for event in events:
            yield event

    async def count_events(self, group: str) -> int:
        def _count() -> int:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM events WHERE group_pubkey = ?", (group,)
            )
            row = cursor.fetchone()
            return int(str(row[0]))

        return await self._run(_count)

    async def get_tips(self, group: str) -> list[str]:
        def _tips() -> list[str]:
            cursor = self.conn.execute(
                """SELECT id FROM events
                   WHERE group_pubkey = ?
                   AND id NOT IN (SELECT DISTINCT parent_id FROM parent_refs)""",
                (group,),
            )
            return [str(row[0]) for row in cursor]

        return await self._run(_tips)

    async def get_known_set(self, group: str) -> frozenset[str]:
        def _get_set() -> frozenset[str]:
            cursor = self.conn.execute("SELECT id FROM events WHERE group_pubkey = ?", (group,))
            return frozenset(str(row[0]) for row in cursor)

        return await self._run(_get_set)

    async def get_parent_map(self, group: str) -> Mapping[str, frozenset[str]]:
        def _get_map() -> dict[str, frozenset[str]]:
            cursor = self.conn.execute(
                """SELECT parent_id, child_id FROM parent_refs
                   WHERE child_id IN (SELECT id FROM events WHERE group_pubkey = ?)""",
                (group,),
            )
            mapping: dict[str, set[str]] = {}
            for parent_id, child_id in cursor:
                key = str(parent_id)
                if key not in mapping:
                    mapping[key] = set()
                mapping[key].add(str(child_id))
            return {k: frozenset(v) for k, v in mapping.items()}

        return await self._run(_get_map)

    async def delete_event(self, event_id: str) -> None:
        def _delete() -> None:
            self.conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            self.conn.execute("DELETE FROM parent_refs WHERE child_id = ?", (event_id,))
            self.conn.execute("DELETE FROM parent_refs WHERE parent_id = ?", (event_id,))
            self.conn.commit()

        await self._run(_delete)

    async def get_hosted_groups(self) -> list[str]:
        def _get_groups() -> list[str]:
            cursor = self.conn.execute("SELECT DISTINCT group_pubkey FROM events")
            return [str(row[0]) for row in cursor]

        return await self._run(_get_groups)

    async def put_event_receipt(self, event_id: str, relay_pubkey: str, event_receipt: EventReceipt) -> None:
        def _put() -> None:
            event_receipt_json = json.dumps(_event_receipt_to_json(event_receipt))
            self.conn.execute(
                "INSERT OR REPLACE INTO event_receipts (event_id, relay_pubkey, event_receipt_json) VALUES (?, ?, ?)",
                (event_id, relay_pubkey, event_receipt_json),
            )
            self.conn.commit()

        await self._run(_put)

    async def get_event_receipt(self, event_id: str, relay_pubkey: str) -> EventReceipt | None:
        def _get() -> EventReceipt | None:
            cursor = self.conn.execute(
                "SELECT event_receipt_json FROM event_receipts WHERE event_id = ? AND relay_pubkey = ?",
                (event_id, relay_pubkey),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return _json_to_event_receipt(json.loads(str(row[0])))

        return await self._run(_get)

    async def iter_event_receipts_for_event(self, event_id: str) -> AsyncIterator[EventReceipt]:
        def _fetch_event_receipts() -> list[EventReceipt]:
            cursor = self.conn.execute(
                "SELECT event_receipt_json FROM event_receipts WHERE event_id = ?", (event_id,)
            )
            return [_json_to_event_receipt(json.loads(str(row[0]))) for row in cursor]

        event_receipts = await self._run(_fetch_event_receipts)
        for event_receipt in event_receipts:
            yield event_receipt

    async def put_fraud_proof(
        self, fp_id: str, group: str, accused_relay: str, event_id: str, fp_json: str
    ) -> None:
        def _put() -> None:
            import time

            self.conn.execute(
                """INSERT OR REPLACE INTO fraud_proofs
                   (id, group_pubkey, accused_relay_pubkey, event_id, fraud_proof_json, received_ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fp_id, group, accused_relay, event_id, fp_json, int(time.time())),
            )
            self.conn.commit()

        await self._run(_put)

    async def query_fraud_proofs(
        self, *, relay: str | None = None, group: str | None = None
    ) -> list[tuple[str, str]]:
        def _query() -> list[tuple[str, str]]:
            query = "SELECT id, fraud_proof_json FROM fraud_proofs WHERE 1=1"
            params: list[str] = []
            if relay is not None:
                query += " AND accused_relay_pubkey = ?"
                params.append(relay)
            if group is not None:
                query += " AND group_pubkey = ?"
                params.append(group)
            cursor = self.conn.execute(query, params)
            return [(str(row[0]), str(row[1])) for row in cursor]

        return await self._run(_query)

    async def save_group_status(self, relay_pubkey: str, group: str, att_json: str, ts: int) -> None:
        def _save() -> None:
            self.conn.execute(
                """INSERT OR REPLACE INTO group_statuses_issued
                   (relay_pubkey, group_pubkey, group_status_json, ts)
                   VALUES (?, ?, ?, ?)""",
                (relay_pubkey, group, att_json, ts),
            )
            self.conn.commit()

        await self._run(_save)

    async def get_last_group_status(self, relay_pubkey: str, group: str) -> str | None:
        def _get() -> str | None:
            cursor = self.conn.execute(
                "SELECT group_status_json FROM group_statuses_issued WHERE relay_pubkey = ? AND group_pubkey = ?",
                (relay_pubkey, group),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return str(row[0])

        return await self._run(_get)

    async def put_heal_provenance(
        self, event_id: str, group: str, witness_pubkeys: list[str], ts: int
    ) -> None:
        def _put() -> None:
            for w in witness_pubkeys:
                self.conn.execute(
                    """INSERT OR IGNORE INTO heal_admission_provenance
                       (event_id, group_pubkey, witness_pubkey, admitted_ts)
                       VALUES (?, ?, ?, ?)""",
                    (event_id, group, w, ts),
                )
            self.conn.commit()

        await self._run(_put)

    async def get_heal_provenance(self, event_id: str) -> list[str]:
        def _get() -> list[str]:
            cursor = self.conn.execute(
                "SELECT witness_pubkey FROM heal_admission_provenance WHERE event_id = ?",
                (event_id,),
            )
            return [str(row[0]) for row in cursor]

        return await self._run(_get)

    async def iter_events_admitted_by(
        self, witness_pubkey: str, group: str | None = None
    ) -> AsyncIterator[str]:
        def _fetch() -> list[str]:
            if group is not None:
                cursor = self.conn.execute(
                    "SELECT event_id FROM heal_admission_provenance "
                    "WHERE witness_pubkey = ? AND group_pubkey = ?",
                    (witness_pubkey, group),
                )
            else:
                cursor = self.conn.execute(
                    "SELECT event_id FROM heal_admission_provenance WHERE witness_pubkey = ?",
                    (witness_pubkey,),
                )
            return [str(row[0]) for row in cursor]

        ids = await self._run(_fetch)
        for eid in ids:
            yield eid

    async def delete_events_admitted_only_by(self, witness_pubkey: str) -> list[str]:
        def _delete() -> list[str]:
            cursor = self.conn.execute(
                """SELECT event_id FROM heal_admission_provenance
                   WHERE witness_pubkey = ?
                   AND event_id NOT IN (
                       SELECT event_id FROM heal_admission_provenance
                       WHERE witness_pubkey != ?
                   )""",
                (witness_pubkey, witness_pubkey),
            )
            orphans = [str(row[0]) for row in cursor]
            for eid in orphans:
                self.conn.execute("DELETE FROM heal_admission_provenance WHERE event_id = ?", (eid,))
                self.conn.execute("DELETE FROM events WHERE id = ?", (eid,))
                self.conn.execute("DELETE FROM parent_refs WHERE child_id = ?", (eid,))
                self.conn.execute("DELETE FROM parent_refs WHERE parent_id = ?", (eid,))
            if orphans:
                self.conn.commit()
            return orphans

        return await self._run(_delete)
