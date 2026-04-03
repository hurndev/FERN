"""RelayWorker — background asyncio relay I/O running in a QThread."""

import asyncio
import json
from threading import Lock
from typing import Any

from PyQt5.QtCore import QObject, QThread, pyqtSignal

import fern.relay as relay
import fern.events as events
from fern.dag import ClientStorage, EventDAG
from fern.sync import decide_sync_action


class RelayWorker(QObject):
    """Runs in a QThread. Owns a persistent asyncio event loop.

    All relay I/O happens here. Protocol logic (sync, validate, store) runs
    entirely in this thread. Communicates results back to the main thread
    via Qt signals with simple scalar/JSON-string payloads.
    """

    sync_complete = pyqtSignal(str, str)
    event_received = pyqtSignal(str, str)
    publish_result = pyqtSignal(str, str)
    relay_status = pyqtSignal(str, str, str)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: QThread | None = None
        self._subscribe_tasks: dict[str, list[asyncio.Task]] = {}
        self._tasks_lock = Lock()
        self._stopping = False
        self._started = False
        self._storage: ClientStorage | None = None
        self._group_locks: dict[str, Lock] = {}

    def set_storage(self, storage: ClientStorage) -> None:
        """Set the ClientStorage instance. Called by controller on main thread before starting."""
        self._storage = storage

    def _get_lock(self, group_pubkey: str) -> Lock:
        """Get or create a lock for a group. Used to serialise DAG access."""
        with self._tasks_lock:
            if group_pubkey not in self._group_locks:
                self._group_locks[group_pubkey] = Lock()
            return self._group_locks[group_pubkey]

    def get_lock(self, group_pubkey: str) -> Lock:
        """Public accessor for the controller to acquire the group lock before
        mutating the DAG from the main thread."""
        return self._get_lock(group_pubkey)

    def _get_dag(self, group_pubkey: str) -> EventDAG:
        """Get the EventDAG for a group. Safe to call from worker thread."""
        if self._storage is None:
            raise RuntimeError(
                "RelayWorker: storage not set. Call set_storage() first."
            )
        return self._storage.get_group_dag(group_pubkey)

    def start(self):
        """Called by QThread. Creates and runs the asyncio loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started = True
        self._loop.run_forever()

    def stop(self):
        """Signal the loop to stop and return immediately (non-blocking)."""
        self._stopping = True
        if not self._loop or self._loop.is_closed():
            return

        async def _shutdown():
            with self._tasks_lock:
                tasks_to_cancel = [
                    task for tasks in self._subscribe_tasks.values() for task in tasks
                ]
                self._subscribe_tasks.clear()
            for task in tasks_to_cancel:
                task.cancel()
            if tasks_to_cancel:
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            self._loop.stop()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)

    def _submit(self, coro):
        """Submit a coroutine to the asyncio loop."""
        if self._stopping or not self._loop or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # --- Main operations ---

    def sync_group(self, group_pubkey: str, relay_urls: list[str]):
        """Run the full sync-and-heal cycle for a group. Emits sync_complete(group_pubkey, summary_json)."""
        self._submit(self._do_sync_group(group_pubkey, relay_urls))

    async def _do_sync_group(self, group_pubkey: str, hint_relays: list[str]):
        """Internally: fetch summaries, decide action, fetch events, validate, store, heal."""
        if not self._storage:
            self.sync_complete.emit(
                group_pubkey, json.dumps({"error": "storage not set"})
            )
            return

        summary: dict[str, Any] = {
            "error": None,
            "hint_relays": list(hint_relays),
            "canonical_relays": [],
            "bad_relays": [],
            "sync_rounds": 0,
            "total_events": 0,
            "invalid_events": 0,
            "healed_events": 0,
            "gaps": [],
            "skipped": False,
            "new_events": 0,
        }

        try:
            dag = self._get_dag(group_pubkey)
            lock = self._get_lock(group_pubkey)

            with lock:
                local_events = dict(dag.events)
                local_event_ids_snapshot = set(local_events.keys())
                local_count = len(local_events)

            local_latest_ts = 0
            if local_count > 0:
                all_local = sorted(
                    local_events.values(), key=lambda e: (e["ts"], e["id"])
                )
                local_latest_ts = all_local[-1]["ts"]

            # Fetch summaries from hint relays
            summary_tasks = [
                relay.fetch_summary(url, group_pubkey) for url in hint_relays
            ]
            summary_results = await asyncio.gather(
                *summary_tasks, return_exceptions=True
            )

            relay_summaries: dict[str, dict] = {}
            for url, s in zip(hint_relays, summary_results):
                if isinstance(s, dict):
                    relay_summaries[url] = s

            if relay_summaries:
                decision = decide_sync_action(
                    set(local_events.keys()), local_latest_ts, relay_summaries
                )
                if decision.action == "skip":
                    summary["skipped"] = True
                    state = events.derive_group_state(local_events.values())
                    summary["canonical_relays"] = (
                        state.relays if state.relays else list(hint_relays)
                    )
                    with lock:
                        gaps = dag.get_missing_parents()
                    summary["gaps"] = sorted(gaps)
                    self.sync_complete.emit(group_pubkey, json.dumps(summary))
                    return

            # --- Full sync with relay discovery ---
            current_relays = list(hint_relays)
            all_validated: dict[str, dict] = dict(local_events)
            seen_relays: set[str] = set()
            all_relay_event_ids: dict[str, set[str]] = {}

            while current_relays:
                summary["sync_rounds"] += 1

                used_this_round = frozenset(current_relays)
                seen_relays.update(current_relays)

                sync_since = local_latest_ts if local_latest_ts > 0 else 0
                skip_genesis = local_latest_ts > 0

                (
                    events_out,
                    relay_event_ids,
                    good_relays,
                    invalid_count,
                ) = await self._fetch_and_validate_events(
                    group_pubkey, current_relays, sync_since, skip_genesis
                )

                summary["invalid_events"] += invalid_count

                new_events = 0
                for eid, event in events_out.items():
                    if eid not in all_validated:
                        all_validated[eid] = event
                        new_events += 1

                summary["new_events"] += new_events

                bad_this_round = [r for r in current_relays if r not in good_relays]
                summary["bad_relays"].extend(bad_this_round)

                if not good_relays:
                    break

                for url, ids in relay_event_ids.items():
                    all_relay_event_ids[url] = ids

                state = events.derive_group_state(all_validated.values())
                derived_relays = state.relays if state.relays else []

                derived_set = frozenset(derived_relays)
                if derived_set == used_this_round or not derived_relays:
                    break

                new_relays = [r for r in derived_relays if r not in seen_relays]
                if not new_relays:
                    break

                current_relays = derived_relays

            summary["total_events"] = len(all_validated)

            # --- Heal canonical relays ---
            state = events.derive_group_state(all_validated.values())
            canonical_relays = state.relays if state.relays else list(seen_relays)
            summary["canonical_relays"] = canonical_relays

            canonical_event_ids: dict[str, set[str]] = {}
            for url in canonical_relays:
                if url in all_relay_event_ids:
                    canonical_event_ids[url] = all_relay_event_ids[url]
                else:
                    fetched_events = await relay.fetch_events(
                        url, group_pubkey, since=sync_since
                    )
                    ids = set()
                    for event in fetched_events:
                        ok, _ = events.verify_event(event)
                        if ok:
                            ids.add(event["id"])
                            if event["id"] not in all_validated:
                                all_validated[event["id"]] = event
                    canonical_event_ids[url] = ids

            events_to_heal = (
                set(all_validated.keys())
                if sync_since == 0
                else (set(all_validated.keys()) - local_event_ids_snapshot)
            )
            healed = 0

            for url in canonical_relays:
                relay_ids = canonical_event_ids.get(url, set())
                missing = events_to_heal - relay_ids
                if missing:
                    for event_id in missing:
                        event = all_validated[event_id]
                        result = await relay.publish(url, event)
                        if result and result.get("type") == "ok":
                            healed += 1

            summary["healed_events"] = healed

            # --- Finalise local storage ---
            with lock:
                new_added = 0
                rejected = 0
                for event in sorted(
                    all_validated.values(), key=lambda e: (e["ts"], e["id"])
                ):
                    ok, reason = dag.add_event(event, skip_verify=True)
                    if ok:
                        new_added += 1
                    elif reason != "duplicate":
                        rejected += 1

                gaps = dag.get_missing_parents()
                summary["gaps"] = sorted(gaps)

            summary["new_events"] = new_added
            summary["total_events"] = dag.count

        except Exception as e:
            summary["error"] = str(e)

        self.sync_complete.emit(group_pubkey, json.dumps(summary))

    async def _fetch_and_validate_events(
        self,
        group_pubkey: str,
        relay_urls: list[str],
        since: int,
        skip_genesis_validation: bool,
    ) -> tuple[dict[str, dict], dict[str, set[str]], list[str], int]:
        """Fetch events from relays, validate them, return validated events."""
        all_validated: dict[str, dict] = {}
        relay_event_ids: dict[str, set[str]] = {}
        good_relays: list[str] = []
        invalid_count = 0

        if skip_genesis_validation:
            good_relays = list(relay_urls)
            valid_genesis = None
        else:
            genesis_tasks = [
                relay.fetch_genesis(url, group_pubkey) for url in relay_urls
            ]
            genesis_results = await asyncio.gather(*genesis_tasks)

            valid_genesis = None
            for url, genesis in zip(relay_urls, genesis_results):
                if genesis is None:
                    continue
                ok, reason = self._validate_genesis(genesis)
                if ok:
                    if valid_genesis is None:
                        valid_genesis = genesis
                    elif genesis["id"] != valid_genesis["id"]:
                        continue
                    good_relays.append(url)
                else:
                    continue

        if not good_relays:
            return {}, {}, [], 0

        fetch_tasks = [
            relay.fetch_events(url, group_pubkey, since) for url in good_relays
        ]
        fetch_results = await asyncio.gather(*fetch_tasks)

        for url, fetched_events in zip(good_relays, fetch_results):
            if not isinstance(fetched_events, list):
                continue
            ids = set()
            for event in fetched_events:
                ok, reason = events.verify_event(event)
                if not ok:
                    invalid_count += 1
                    continue
                ids.add(event["id"])
                if event["id"] not in all_validated:
                    all_validated[event["id"]] = event
            relay_event_ids[url] = ids

        return all_validated, relay_event_ids, good_relays, invalid_count

    def _validate_genesis(self, event: dict) -> tuple[bool, str]:
        """Validate a genesis event."""
        if not event:
            return False, "no event"
        required = ["id", "type", "group", "author", "parents", "content", "ts", "sig"]
        for field in required:
            if field not in event:
                return False, f"missing field: {field}"
        if event["type"] != "group_genesis":
            return False, "not a genesis event"
        if event["parents"]:
            return False, "genesis must have empty parents"
        if not events.verify_event_id(event):
            return False, "event ID mismatch"
        if not events.verify_event_signature(event, event["group"]):
            return False, "invalid signature"
        return True, "ok"

    def publish_event(self, event: dict, relay_urls: list[str]):
        """Publish event to all relays. Emits publish_result(event_id, results_json)."""
        self._submit(self._do_publish_event(event, relay_urls))

    async def _do_publish_event(self, event: dict, relay_urls: list[str]):
        """Publish event to all relays in parallel."""
        results = await relay.publish_to_all(relay_urls, event)
        results_json = {
            url: (
                None
                if r is None
                else {"type": r.get("type"), "message": r.get("message")}
            )
            if isinstance(r, dict)
            else str(r)
            for url, r in results.items()
        }
        self.publish_result.emit(event["id"], json.dumps(results_json))

    def start_subscriptions(self, group_pubkey: str, relay_urls: list[str]):
        """Open persistent subscribe connections to all relays.
        Emits: event_received(group_pubkey, event_json), relay_status(group_pubkey, relay_url, status)"""
        self._submit(self._do_start_subscriptions(group_pubkey, relay_urls))

    async def _do_start_subscriptions(self, group_pubkey: str, relay_urls: list[str]):
        """Run subscribe connections for a group."""

        async def on_event(event: dict, relay_url: str):
            group = event.get("group", "")
            if not group:
                return

            lock = self._get_lock(group)
            dag = self._get_dag(group)

            valid, reason = events.verify_event(event)
            if not valid:
                print(
                    f"[WORKER] subscribe event rejected: {reason} id={event.get('id', '?')[:16]}..."
                )
                return

            with lock:
                already_had = event["id"] in dag.events
                ok, reason = dag.add_event(event, skip_verify=True)
                if ok and not already_had:
                    self.event_received.emit(group, json.dumps(event))

            self.relay_status.emit(group, relay_url, "connected")

        def on_connect(url):
            self.relay_status.emit(group_pubkey, url, "connected")

        def on_reconnect(url):
            self.relay_status.emit(group_pubkey, url, "reconnecting")

        def on_error(url, exc):
            print(f"[WORKER] subscribe error on {url}: {exc}")
            self.relay_status.emit(group_pubkey, url, "disconnected")

        async def _subscribe_one(url: str):
            try:
                await relay.subscribe_with_retry(
                    url,
                    group_pubkey,
                    on_event,
                    on_connect=on_connect,
                    on_reconnect=on_reconnect,
                    on_error=on_error,
                )
            except asyncio.CancelledError:
                raise

        tasks = []
        for url in relay_urls:
            task = asyncio.create_task(_subscribe_one(url))
            tasks.append(task)

        with self._tasks_lock:
            if group_pubkey not in self._subscribe_tasks:
                self._subscribe_tasks[group_pubkey] = []
            self._subscribe_tasks[group_pubkey].extend(tasks)

    def stop_subscriptions(self, group_pubkey: str):
        """Cancel subscribe tasks for a group."""
        if not self._loop or self._loop.is_closed():
            return

        async def _cancel_tasks():
            with self._tasks_lock:
                if group_pubkey not in self._subscribe_tasks:
                    return
                tasks = list(self._subscribe_tasks.pop(group_pubkey, []))
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(_cancel_tasks(), self._loop)
