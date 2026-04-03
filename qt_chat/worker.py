"""RelayWorker — background asyncio relay I/O running in a QThread."""

import asyncio
import json
from dataclasses import asdict
from threading import Lock

from PyQt5.QtCore import QObject, QThread, pyqtSignal

import fern.relay as relay
import fern.events as events
from fern.dag import ClientStorage, EventDAG
from fern.sync import run_sync_and_heal


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
        self._submit(self._do_sync(group_pubkey, relay_urls))

    async def _do_sync(self, group_pubkey: str, hint_relays: list[str]):
        """Run sync-and-heal using shared implementation."""
        if not self._storage:
            self.sync_complete.emit(
                group_pubkey, json.dumps({"error": "storage not set"})
            )
            return
        dag = self._get_dag(group_pubkey)
        lock = self._get_lock(group_pubkey)
        result = await run_sync_and_heal(dag, hint_relays, lock=lock)
        self.sync_complete.emit(group_pubkey, json.dumps(asdict(result)))

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
