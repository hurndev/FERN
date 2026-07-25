from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from fern.bft.application import ApplicationState, ChainHead, genesis_chain_head
from fern.bft.blocks import Commit, Proposal
from fern.bft.certificates import TimestampObservation, Vote
from fern.bft.chain import verify_and_apply_commit
from fern.bft.consensus import DoubleVoteError, SafetyJournal, SafetyState
from fern.bft.manifest import AdmissionRecord
from fern.events.event import Event


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class BFTStore(SafetyJournal):
    """SQLite persistence for BFT history, mempool and crash-safety state."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self._create_schema()

    def _configure(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _create_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bft_groups (
                    group_pubkey TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL UNIQUE,
                    genesis_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    height INTEGER NOT NULL,
                    block_hash TEXT NOT NULL,
                    history_root TEXT NOT NULL,
                    logical_bytes INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bft_commits (
                    group_pubkey TEXT NOT NULL,
                    height INTEGER NOT NULL,
                    block_hash TEXT NOT NULL,
                    commit_json TEXT NOT NULL,
                    PRIMARY KEY (group_pubkey, height),
                    UNIQUE (group_pubkey, block_hash),
                    FOREIGN KEY (group_pubkey) REFERENCES bft_groups(group_pubkey)
                );

                CREATE TABLE IF NOT EXISTS bft_events (
                    id TEXT PRIMARY KEY,
                    group_pubkey TEXT NOT NULL,
                    author TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    first_seen_ms INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','finalized')),
                    height INTEGER,
                    position INTEGER,
                    certified_time_ms INTEGER,
                    UNIQUE (group_pubkey, height, position),
                    FOREIGN KEY (group_pubkey) REFERENCES bft_groups(group_pubkey)
                );
                CREATE INDEX IF NOT EXISTS bft_events_group_status
                    ON bft_events(group_pubkey, status, first_seen_ms, id);
                CREATE INDEX IF NOT EXISTS bft_events_author_seq
                    ON bft_events(group_pubkey, author, seq);

                CREATE TABLE IF NOT EXISTS bft_safety_state (
                    group_pubkey TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (group_pubkey, chain_id, epoch, height)
                );

                CREATE TABLE IF NOT EXISTS bft_own_votes (
                    group_pubkey TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    block_id TEXT,
                    vote_json TEXT NOT NULL,
                    PRIMARY KEY (group_pubkey, chain_id, epoch, height, round, phase)
                );

                CREATE TABLE IF NOT EXISTS bft_votes (
                    group_pubkey TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    validator TEXT NOT NULL,
                    block_id TEXT,
                    block_key TEXT NOT NULL,
                    vote_json TEXT NOT NULL,
                    PRIMARY KEY (group_pubkey, epoch, height, round, phase, validator, block_key)
                );

                CREATE TABLE IF NOT EXISTS bft_observations (
                    group_pubkey TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    candidate_id TEXT NOT NULL,
                    validator TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    PRIMARY KEY (group_pubkey, epoch, height, round, candidate_id, validator)
                );

                CREATE TABLE IF NOT EXISTS bft_own_observations (
                    group_pubkey TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    candidate_id TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    PRIMARY KEY (group_pubkey, epoch, height, round)
                );

                CREATE TABLE IF NOT EXISTS bft_equivocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_pubkey TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    validator TEXT NOT NULL,
                    first_json TEXT NOT NULL,
                    second_json TEXT NOT NULL,
                    observed_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bft_admissions (
                    group_pubkey TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL
                );
                """
            )

    def bootstrap_genesis(self, genesis: Event) -> ChainHead:
        head = genesis_chain_head(genesis)
        with self._lock:
            existing = self.conn.execute(
                "SELECT genesis_json FROM bft_groups WHERE group_pubkey = ?", (genesis.group,)
            ).fetchone()
            encoded = _json(genesis.to_dict())
            if existing is not None:
                if str(existing["genesis_json"]) != encoded:
                    raise ValueError("conflicting genesis for hosted group")
                return self.get_chain_head(genesis.group)
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    """INSERT INTO bft_groups
                       (group_pubkey, chain_id, genesis_json, state_json, height,
                        block_hash, history_root, logical_bytes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        genesis.group,
                        head.state.chain_id,
                        encoded,
                        _json(head.state.to_dict()),
                        head.height,
                        head.block_hash,
                        head.history_root,
                        head.logical_bytes,
                    ),
                )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return head

    def hosted_groups(self) -> tuple[str, ...]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT group_pubkey FROM bft_groups ORDER BY group_pubkey"
            ).fetchall()
        return tuple(str(row["group_pubkey"]) for row in rows)

    def save_admission(self, record: AdmissionRecord) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO bft_admissions (group_pubkey, record_json)
                   VALUES (?, ?)
                   ON CONFLICT(group_pubkey) DO UPDATE SET record_json=excluded.record_json""",
                (record.group, _json(record.to_dict())),
            )

    def get_admission(self, group: str) -> AdmissionRecord | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT record_json FROM bft_admissions WHERE group_pubkey = ?", (group,)
            ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["record_json"]))
        if not isinstance(value, dict):
            raise ValueError("invalid persisted admission record")
        return AdmissionRecord.from_dict(value)

    def get_genesis(self, group: str) -> Event | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT genesis_json FROM bft_groups WHERE group_pubkey = ?", (group,)
            ).fetchone()
        return Event.from_dict(json.loads(str(row["genesis_json"]))) if row else None

    def get_chain_head(self, group: str) -> ChainHead:
        with self._lock:
            row = self.conn.execute(
                """SELECT state_json, height, block_hash, history_root, logical_bytes
                   FROM bft_groups WHERE group_pubkey = ?""",
                (group,),
            ).fetchone()
        if row is None:
            raise KeyError(f"group is not hosted: {group}")
        return ChainHead(
            height=int(row["height"]),
            block_hash=str(row["block_hash"]),
            history_root=str(row["history_root"]),
            logical_bytes=int(row["logical_bytes"]),
            state=ApplicationState.from_dict(json.loads(str(row["state_json"]))),
        )

    def add_pending(self, event: Event, first_seen_ms: int | None = None) -> int:
        if event.id is None:
            raise ValueError("cannot persist unsigned event")
        seen = first_seen_ms or int(time.time() * 1000)
        with self._lock:
            existing = self.conn.execute(
                "SELECT event_json, first_seen_ms FROM bft_events WHERE id = ?", (event.id,)
            ).fetchone()
            encoded = _json(event.to_dict())
            if existing is not None:
                if str(existing["event_json"]) != encoded:
                    raise ValueError("event ID collision")
                return int(existing["first_seen_ms"])
            self.conn.execute(
                """INSERT INTO bft_events
                   (id, group_pubkey, author, seq, event_json, first_seen_ms, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (event.id, event.group, event.author, event.seq, encoded, seen),
            )
        return seen

    def next_pending_sequence(self, group: str, author: str, finalized_seq: int) -> int:
        """Return the next contiguous sequence accepted into the local mempool."""

        with self._lock:
            rows = self.conn.execute(
                """SELECT seq FROM bft_events
                   WHERE group_pubkey = ? AND author = ? AND status = 'pending'
                   ORDER BY seq""",
                (group, author),
            ).fetchall()
        expected = finalized_seq + 1
        for row in rows:
            sequence = int(row["seq"])
            if sequence == expected:
                expected += 1
            elif sequence > expected:
                break
        return expected

    def pending_at_sequence(self, group: str, author: str, seq: int) -> Event | None:
        with self._lock:
            row = self.conn.execute(
                """SELECT event_json FROM bft_events
                   WHERE group_pubkey = ? AND author = ? AND seq = ? AND status = 'pending'
                   ORDER BY first_seen_ms, id LIMIT 1""",
                (group, author, seq),
            ).fetchone()
        return Event.from_dict(json.loads(str(row["event_json"]))) if row else None

    def first_seen_ms(self, event_id: str) -> int | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT first_seen_ms FROM bft_events WHERE id = ?", (event_id,)
            ).fetchone()
        return int(row["first_seen_ms"]) if row else None

    def remove_pending(self, event_id: str) -> None:
        """Discard an event only while it is still an uncommitted mempool item."""

        with self._lock:
            self.conn.execute(
                "DELETE FROM bft_events WHERE id = ? AND status = 'pending'", (event_id,)
            )

    def pending_events(self, group: str, limit: int = 500) -> tuple[Event, ...]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT event_json FROM bft_events
                   WHERE group_pubkey = ? AND status = 'pending'
                   ORDER BY first_seen_ms, author, seq, id LIMIT ?""",
                (group, limit),
            ).fetchall()
        return tuple(Event.from_dict(json.loads(str(row["event_json"]))) for row in rows)

    def finalized_events(self, group: str) -> tuple[tuple[Event, int, int, int], ...]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT event_json, height, position, certified_time_ms
                   FROM bft_events WHERE group_pubkey = ? AND status = 'finalized'
                   ORDER BY height, position""",
                (group,),
            ).fetchall()
        return tuple(
            (
                Event.from_dict(json.loads(str(row["event_json"]))),
                int(row["height"]),
                int(row["position"]),
                int(row["certified_time_ms"]),
            )
            for row in rows
        )

    def event_count(self, group: str, *, finalized_only: bool = True) -> int:
        query = "SELECT COUNT(*) AS count FROM bft_events WHERE group_pubkey = ?"
        if finalized_only:
            query += " AND status = 'finalized'"
        with self._lock:
            row = self.conn.execute(query, (group,)).fetchone()
        return int(row["count"]) if row is not None else 0

    def get_event(self, event_id: str) -> tuple[Event, str, int | None, int | None] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT event_json, status, height, certified_time_ms FROM bft_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return (
            Event.from_dict(json.loads(str(row["event_json"]))),
            str(row["status"]),
            int(row["height"]) if row["height"] is not None else None,
            int(row["certified_time_ms"]) if row["certified_time_ms"] is not None else None,
        )

    def save_commit(self, group: str, commit: Commit) -> ChainHead:
        with self._lock:
            current = self.get_chain_head(group)
            next_head = verify_and_apply_commit(current, commit)
            event_rows = list(
                zip(commit.block.candidate.all_events, commit.block.certified_times_ms, strict=True)
            )
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    """INSERT INTO bft_commits
                       (group_pubkey, height, block_hash, commit_json)
                       VALUES (?, ?, ?, ?)""",
                    (group, commit.block.height, commit.block.id, _json(commit.to_dict())),
                )
                for position, (event, certified_ms) in enumerate(event_rows):
                    assert event.id is not None
                    first_seen = self.first_seen_ms(event.id) or certified_ms
                    # A user can equivocate at one sequence. Consensus chooses
                    # one value; discard any conflicting local mempool copy.
                    self.conn.execute(
                        """DELETE FROM bft_events
                           WHERE group_pubkey = ? AND author = ? AND seq = ?
                             AND status = 'pending' AND id != ?""",
                        (event.group, event.author, event.seq, event.id),
                    )
                    self.conn.execute(
                        """INSERT INTO bft_events
                           (id, group_pubkey, author, seq, event_json, first_seen_ms,
                            status, height, position, certified_time_ms)
                           VALUES (?, ?, ?, ?, ?, ?, 'finalized', ?, ?, ?)
                           ON CONFLICT(id) DO UPDATE SET
                             status='finalized', height=excluded.height,
                             position=excluded.position,
                             certified_time_ms=excluded.certified_time_ms""",
                        (
                            event.id,
                            event.group,
                            event.author,
                            event.seq,
                            _json(event.to_dict()),
                            first_seen,
                            commit.block.height,
                            position,
                            certified_ms,
                        ),
                    )
                self.conn.execute(
                    """UPDATE bft_groups SET state_json = ?, height = ?, block_hash = ?,
                       history_root = ?, logical_bytes = ? WHERE group_pubkey = ?""",
                    (
                        _json(next_head.state.to_dict()),
                        next_head.height,
                        next_head.block_hash,
                        next_head.history_root,
                        next_head.logical_bytes,
                        group,
                    ),
                )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return next_head

    def commits(self, group: str, from_height: int = 1) -> tuple[Commit, ...]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT commit_json FROM bft_commits
                   WHERE group_pubkey = ? AND height >= ? ORDER BY height""",
                (group, from_height),
            ).fetchall()
        return tuple(Commit.from_dict(json.loads(str(row["commit_json"]))) for row in rows)

    def load_safety_state(
        self, group: str, chain_id: str, epoch: int, height: int
    ) -> SafetyState | None:
        with self._lock:
            row = self.conn.execute(
                """SELECT state_json FROM bft_safety_state
                   WHERE group_pubkey = ? AND chain_id = ? AND epoch = ? AND height = ?""",
                (group, chain_id, epoch, height),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["state_json"]))
        raw_locked = value.get("locked_proposal")
        raw_valid = value.get("valid_proposal")
        return SafetyState(
            group=group,
            chain_id=chain_id,
            epoch=epoch,
            height=height,
            round=int(value.get("round", 0)),
            locked_round=(
                int(value["locked_round"]) if value.get("locked_round") is not None else None
            ),
            locked_block_id=(
                str(value["locked_block_id"]) if value.get("locked_block_id") is not None else None
            ),
            locked_proposal=Proposal.from_dict(raw_locked)
            if isinstance(raw_locked, dict)
            else None,
            valid_round=(
                int(value["valid_round"]) if value.get("valid_round") is not None else None
            ),
            valid_proposal=Proposal.from_dict(raw_valid) if isinstance(raw_valid, dict) else None,
        )

    def save_safety_state(self, state: SafetyState) -> None:
        value = {
            "round": state.round,
            "locked_round": state.locked_round,
            "locked_block_id": state.locked_block_id,
            "locked_proposal": (
                state.locked_proposal.to_dict() if state.locked_proposal is not None else None
            ),
            "valid_round": state.valid_round,
            "valid_proposal": (
                state.valid_proposal.to_dict() if state.valid_proposal is not None else None
            ),
        }
        with self._lock:
            self.conn.execute(
                """INSERT INTO bft_safety_state
                   (group_pubkey, chain_id, epoch, height, state_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(group_pubkey, chain_id, epoch, height)
                   DO UPDATE SET state_json = excluded.state_json""",
                (state.group, state.chain_id, state.epoch, state.height, _json(value)),
            )

    def record_own_vote(self, vote: Vote) -> Vote:
        key = (vote.group, vote.chain_id, vote.epoch, vote.height, vote.round, vote.phase)
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    """SELECT vote_json, block_id FROM bft_own_votes
                       WHERE group_pubkey = ? AND chain_id = ? AND epoch = ?
                         AND height = ? AND round = ? AND phase = ?""",
                    key,
                ).fetchone()
                if row is not None:
                    existing = Vote.from_dict(json.loads(str(row["vote_json"])))
                    if existing.block_id != vote.block_id:
                        raise DoubleVoteError("refusing to persist a conflicting own vote")
                    self.conn.execute("COMMIT")
                    return existing
                self.conn.execute(
                    """INSERT INTO bft_own_votes
                       (group_pubkey, chain_id, epoch, height, round, phase, block_id, vote_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*key, vote.block_id, _json(vote.to_dict())),
                )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return vote

    def save_vote(self, vote: Vote) -> None:
        with self._lock:
            rows = self.conn.execute(
                """SELECT vote_json, block_id FROM bft_votes
                   WHERE group_pubkey = ? AND epoch = ? AND height = ? AND round = ?
                     AND phase = ? AND validator = ?""",
                (vote.group, vote.epoch, vote.height, vote.round, vote.phase, vote.validator),
            ).fetchall()
            for row in rows:
                if row["block_id"] != vote.block_id:
                    self.conn.execute(
                        """INSERT INTO bft_equivocations
                           (group_pubkey, epoch, height, round, kind, validator,
                            first_json, second_json, observed_ms)
                           VALUES (?, ?, ?, ?, 'vote', ?, ?, ?, ?)""",
                        (
                            vote.group,
                            vote.epoch,
                            vote.height,
                            vote.round,
                            vote.validator,
                            str(row["vote_json"]),
                            _json(vote.to_dict()),
                            int(time.time() * 1000),
                        ),
                    )
            self.conn.execute(
                """INSERT OR IGNORE INTO bft_votes
                   (group_pubkey, epoch, height, round, phase, validator,
                    block_id, block_key, vote_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vote.group,
                    vote.epoch,
                    vote.height,
                    vote.round,
                    vote.phase,
                    vote.validator,
                    vote.block_id,
                    vote.block_id or "",
                    _json(vote.to_dict()),
                ),
            )

    def votes(
        self, group: str, epoch: int, height: int, round: int, phase: str
    ) -> tuple[Vote, ...]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT vote_json FROM bft_votes
                   WHERE group_pubkey = ? AND epoch = ? AND height = ?
                     AND round = ? AND phase = ?""",
                (group, epoch, height, round, phase),
            ).fetchall()
        return tuple(Vote.from_dict(json.loads(str(row["vote_json"]))) for row in rows)

    def record_own_observation(self, observation: TimestampObservation) -> TimestampObservation:
        key = (observation.group, observation.epoch, observation.height, observation.round)
        with self._lock:
            row = self.conn.execute(
                """SELECT candidate_id, observation_json FROM bft_own_observations
                   WHERE group_pubkey = ? AND epoch = ? AND height = ? AND round = ?""",
                key,
            ).fetchone()
            if row is not None:
                if str(row["candidate_id"]) != observation.candidate_id:
                    raise DoubleVoteError("refusing to observe conflicting round candidates")
                return TimestampObservation.from_dict(json.loads(str(row["observation_json"])))
            self.conn.execute(
                """INSERT INTO bft_own_observations
                   (group_pubkey, epoch, height, round, candidate_id, observation_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (*key, observation.candidate_id, _json(observation.to_dict())),
            )
        return observation

    def save_observation(self, observation: TimestampObservation) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR IGNORE INTO bft_observations
                   (group_pubkey, epoch, height, round, candidate_id,
                    validator, observation_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.group,
                    observation.epoch,
                    observation.height,
                    observation.round,
                    observation.candidate_id,
                    observation.validator,
                    _json(observation.to_dict()),
                ),
            )

    def observations(
        self, group: str, epoch: int, height: int, round: int, candidate_id: str
    ) -> tuple[TimestampObservation, ...]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT observation_json FROM bft_observations
                   WHERE group_pubkey = ? AND epoch = ? AND height = ?
                     AND round = ? AND candidate_id = ? ORDER BY validator""",
                (group, epoch, height, round, candidate_id),
            ).fetchall()
        return tuple(
            TimestampObservation.from_dict(json.loads(str(row["observation_json"]))) for row in rows
        )


__all__ = ["BFTStore"]
