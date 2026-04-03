"""ChatController — thin orchestration bridge between app.py and worker.py."""

import json
from typing import Callable

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from fern import crypto, events, dag, config, storage
from .worker import RelayWorker


class ChatController(QObject):
    """Owns ClientStorage and RelayWorker. Thin orchestration — no protocol logic.

    Receives UI actions, delegates to worker, routes worker results to UI.
    Protocol logic (sync decisions, validation, storage) runs in the worker thread.
    """

    group_created = pyqtSignal(str)
    group_joined = pyqtSignal(str)
    group_left = pyqtSignal(str)
    sync_finished = pyqtSignal(str)
    event_for_ui = pyqtSignal(str, dict)
    state_changed = pyqtSignal(str)
    publish_ok = pyqtSignal(str)
    publish_failed = pyqtSignal(str, str)
    error = pyqtSignal(str)
    log_message = pyqtSignal(str, str)
    relay_status = pyqtSignal(str, str, str)
    relays_changed = pyqtSignal(str, list)

    def __init__(self, fern_home: str | None = None):
        super().__init__()
        self.storage = dag.ClientStorage(str(storage.resolve_fern_dir(fern_home)))
        self.user_privkey: str = ""
        self.user_pubkey: str = ""
        self._active_syncs: set[str] = set()
        self._group_info_cache: dict[str, dict] = {}

        self._worker = RelayWorker()
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.start)

        self._worker.set_storage(self.storage)

        self._worker.sync_complete.connect(self._on_sync_complete)
        self._worker.event_received.connect(self._on_event_received)
        self._worker.publish_result.connect(self._on_publish_result)
        self._worker.relay_status.connect(self._on_relay_status)
        self._worker.error.connect(self._on_worker_error)

        self._pending_publishes: dict[str, dict] = {}
        self._pending_actions: dict[str, tuple] = {}
        self._worker_thread.start()

    def _on_worker_error(self, msg: str):
        self._log("error", msg)

    def _on_relay_status(self, group_pubkey: str, relay_url: str, status: str):
        self.relay_status.emit(group_pubkey, relay_url, status)

    def _log(self, level: str, message: str):
        """Log a message at the given level and emit for UI."""
        self.log_message.emit(level, message)
        if level == "error":
            self.error.emit(message)

    def has_identity(self) -> bool:
        """Check if user key exists."""
        import os

        return os.path.exists(self.storage.get_user_key_path())

    def load_identity(self) -> bool:
        """Load existing identity from storage. Returns True if loaded."""
        import os

        paths_to_try = [self.storage.get_user_key_path()]
        fallback_key = str(self.storage.base_dir / "keys" / "user.pem")
        if fallback_key not in paths_to_try:
            paths_to_try.append(fallback_key)
        home_path = os.path.expanduser("~/.fern/keys/user.pem")
        if home_path not in paths_to_try:
            paths_to_try.append(home_path)
        for key_path in paths_to_try:
            try:
                self.user_privkey = crypto.load_private_key(key_path)
                self.user_pubkey = crypto.public_key_from_private(self.user_privkey)
                print(f"[FERN] Identity loaded from: {key_path}")
                return True
            except Exception:
                continue
        return False

    def generate_identity(self) -> tuple[str, str]:
        """Generate a new identity. Returns (privkey, pubkey)."""
        self.user_privkey, self.user_pubkey = crypto.generate_keypair()
        crypto.save_keypair(self.user_privkey, self.storage.get_user_key_path())
        print(f"[FERN] New identity generated: {self.user_pubkey[:16]}...")
        return self.user_privkey, self.user_pubkey

    def import_identity(self, privkey_hex: str) -> tuple[str, str]:
        """Import identity from hex private key. Returns (privkey, pubkey)."""
        privkey_hex = privkey_hex.strip()
        if len(privkey_hex) < 64 or not all(
            c in "0123456789abcdefABCDEF" for c in privkey_hex
        ):
            raise ValueError("Invalid private key format: must be 64 hex characters")
        self.user_privkey = privkey_hex
        self.user_pubkey = crypto.public_key_from_private(self.user_privkey)
        crypto.save_keypair(self.user_privkey, self.storage.get_user_key_path())
        print(f"[FERN] Identity imported: {self.user_pubkey[:16]}...")
        return self.user_privkey, self.user_pubkey

    def shutdown(self):
        """Stop worker thread, clean up."""
        self._worker.stop()
        self._worker_thread.quit()
        self._worker_thread.wait()

    def get_identity(self) -> tuple[str, str]:
        """Returns (pubkey, privkey)."""
        return self.user_pubkey, self.user_privkey

    def _with_lock(self, group_pubkey: str, fn: Callable):
        """Run fn while holding the group lock. For safe main-thread DAG reads."""
        lock = self._worker.get_lock(group_pubkey)
        with lock:
            return fn()

    def _build_group_info(self, group_pubkey: str) -> dict:
        """Build group info dict from DAG. Caller must hold lock."""
        dag_obj = self.storage.get_group_dag(group_pubkey)
        state = dag_obj.get_state()
        events_list = dag_obj.get_all_events()
        return {
            "pubkey": group_pubkey,
            "name": state.metadata.get("name", f"Group_{group_pubkey[:8]}"),
            "description": state.metadata.get("description", ""),
            "public": state.public,
            "event_count": len(events_list),
            "member_count": len(state.joined),
            "relays": state.relays,
            "joined": self.user_pubkey in state.joined,
        }

    def _refresh_group_cache(self, group_pubkey: str):
        """Refresh cached group info for one group."""
        lock = self._worker.get_lock(group_pubkey)
        with lock:
            self._group_info_cache[group_pubkey] = self._build_group_info(group_pubkey)

    def _populate_group_cache(self):
        """Populate cache for all known groups. Called on startup."""
        for group_pubkey in self.storage.list_groups():
            self._refresh_group_cache(group_pubkey)

    def list_groups(self) -> list[dict]:
        """Returns list of cached group info dicts."""
        return list(self._group_info_cache.values())

    def get_group_events(self, group_pubkey: str) -> list[dict]:
        """Returns sorted events for display (thread-safe)."""
        dag_obj = self.storage.get_group_dag(group_pubkey)
        return self._with_lock(group_pubkey, dag_obj.get_all_events)

    def get_group_state(self, group_pubkey: str):
        """Returns current derived state for a group (thread-safe)."""
        dag_obj = self.storage.get_group_dag(group_pubkey)
        return self._with_lock(group_pubkey, dag_obj.get_state)

    def is_joined(self, group_pubkey: str) -> bool:
        """Check if the user is in the joined set for this group (thread-safe)."""
        dag_obj = self.storage.get_group_dag(group_pubkey)
        state = self._with_lock(group_pubkey, dag_obj.get_state)
        return self.user_pubkey in state.joined

    def is_mod(self, group_pubkey: str) -> bool:
        """Check if the user is a mod for this group (thread-safe)."""
        dag_obj = self.storage.get_group_dag(group_pubkey)
        state = self._with_lock(group_pubkey, dag_obj.get_state)
        return self.user_pubkey in state.mods

    def sync_group(self, group_pubkey: str, hint_relays: list[str] | None = None):
        """Start the sync flow for a group. Async, non-blocking."""
        if group_pubkey in self._active_syncs:
            return
        try:
            self._active_syncs.add(group_pubkey)
            dag_obj = self.storage.get_group_dag(group_pubkey)
            lock = self._worker.get_lock(group_pubkey)
            with lock:
                state = dag_obj.get_state()
            relay_urls = (
                state.relays
                if state.relays
                else (hint_relays or config.BOOTSTRAP_RELAYS)
            )
            self._worker.sync_group(group_pubkey, relay_urls)
            self._log("info", f"Syncing with {len(relay_urls)} relay(s)...")
        except Exception as e:
            self._log("error", f"Failed to sync group: {e}")
            self._active_syncs.discard(group_pubkey)

    def _on_sync_complete(self, group_pubkey: str, summary_json: str):
        """Handle sync completion. Route to pending action if any, else notify UI."""
        self._active_syncs.discard(group_pubkey)

        try:
            summary = json.loads(summary_json)
        except Exception:
            self._log("error", "sync_complete: failed to parse summary JSON")
            self.sync_finished.emit(group_pubkey)
            return

        if summary.get("error"):
            self._log("error", f"Sync failed: {summary['error']}")
        else:
            new_events = summary.get("new_events", 0)
            healed = summary.get("healed_events", 0)
            skipped = summary.get("skipped", False)
            self._log(
                "info",
                f"Sync complete: {new_events} new events, {healed} healed, skipped={skipped}",
            )

        self._refresh_group_cache(group_pubkey)
        self.state_changed.emit(group_pubkey)
        self.sync_finished.emit(group_pubkey)

        action = self._pending_actions.pop(group_pubkey, None)
        if action is None:
            return

        name, *args = action
        if name == "join_after_sync":
            hint_relays = args[0] if args else []
            self._complete_join(group_pubkey, hint_relays)

    def _complete_join(self, group_pubkey: str, hint_relays: list[str]):
        """Finish joining after sync completes."""
        try:
            dag_obj = self.storage.get_group_dag(group_pubkey)
            lock = self._worker.get_lock(group_pubkey)
            with lock:
                state = dag_obj.get_state()
                count = dag_obj.count

            if self.user_pubkey in state.joined:
                self.group_joined.emit(group_pubkey)
                return

            if count == 0:
                self._log(
                    "error",
                    "Sync failed: no events received (is the genesis event on this relay?)",
                )
                return

            if not state.public and self.user_pubkey not in state.members:
                self._log(
                    "warning", "This group is private and you have not been invited"
                )
                return

            with lock:
                tips = dag_obj.get_tips()
            join_event = events.create_group_join(
                group_pubkey, self.user_pubkey, self.user_privkey, tips
            )
            with lock:
                dag_obj.add_event(join_event, skip_verify=True, skip_auth=True)
            self.event_for_ui.emit(group_pubkey, join_event)

            relay_urls = state.relays if state.relays else hint_relays
            self._pending_publishes[join_event["id"]] = {
                "group_pubkey": group_pubkey,
                "event": join_event,
            }
            self._worker.publish_event(join_event, relay_urls)

            self._refresh_group_cache(group_pubkey)
            self.state_changed.emit(group_pubkey)
            self.group_joined.emit(group_pubkey)
            self._log("info", "Joined group successfully")
        except Exception as e:
            self._log("error", f"Failed to join group: {e}")

    def _on_event_received(self, group_pubkey: str, event_json: str):
        """Handle incoming event from subscription — parse JSON and emit for UI only."""
        try:
            event = json.loads(event_json)
        except Exception:
            return
        self.event_for_ui.emit(group_pubkey, event)

        if event.get("type") == "relay_update":
            lock = self._worker.get_lock(group_pubkey)
            with lock:
                dag_obj = self.storage.get_group_dag(group_pubkey)
                state = dag_obj.get_state()
                self.relays_changed.emit(group_pubkey, list(state.relays))

    def _on_publish_result(self, event_id: str, results_json: str):
        """Handle publish result. If all failed, remove optimistic event."""
        pending = self._pending_publishes.pop(event_id, None)
        if pending:
            group_pubkey = pending["group_pubkey"]
            dag_obj = self.storage.get_group_dag(group_pubkey)

            try:
                results = json.loads(results_json)
            except Exception:
                self.publish_failed.emit(event_id, "failed to parse results")
                return

            successes = sum(
                1
                for r in results.values()
                if r is not None and isinstance(r, dict) and r.get("type") == "ok"
            )
            if successes == 0:
                dag_obj.remove_event(event_id)
                self.publish_failed.emit(event_id, "All relays rejected the event")
                return

            self.publish_ok.emit(event_id)
        else:
            self.publish_ok.emit(event_id)

    def _publish_action(
        self,
        group_pubkey: str,
        create_fn: Callable,
        check_fn: Callable | None = None,
        post_fn: Callable | None = None,
    ):
        """Create and publish an event.

        Args:
            create_fn: (tips) -> event | None. Return None to abort.
            check_fn: (state) -> None. Raise or return. If None, no check.
            post_fn: (event) -> None. Called after optimistic add, before publish.
        """
        try:
            dag_obj = self.storage.get_group_dag(group_pubkey)
            lock = self._worker.get_lock(group_pubkey)

            with lock:
                state = dag_obj.get_state()
                tips = dag_obj.get_tips()

            if check_fn:
                check_fn(state)

            event = create_fn(tips)
            if event is None:
                return

            with lock:
                dag_obj.add_event(event, skip_verify=True, skip_auth=True)

            self.event_for_ui.emit(group_pubkey, event)

            if post_fn:
                post_fn(event)

            relay_urls = state.relays if state.relays else config.BOOTSTRAP_RELAYS
            self._pending_publishes[event["id"]] = {
                "group_pubkey": group_pubkey,
                "event": event,
            }
            self._worker.publish_event(event, relay_urls)
            self._refresh_group_cache(group_pubkey)
            self.state_changed.emit(group_pubkey)
        except Exception as e:
            self._log("error", f"Action failed: {e}")

    def send_message(self, group_pubkey: str, text: str):
        """Create and publish a message event."""

        def check(state):
            if not state.can_post(self.user_pubkey):
                raise PermissionError("Must join before sending messages")

        def create(tips):
            return events.create_message(
                group_pubkey, self.user_pubkey, self.user_privkey, text, tips
            )

        def post(event):
            self._log("info", "Message sent to group")

        self._publish_action(group_pubkey, create, check, post)

    def create_group(
        self, name: str, description: str, public: bool, relay_urls: list[str]
    ):
        """Create a new group."""
        try:
            group_privkey, group_pubkey = crypto.generate_keypair()
            self.storage.save_group_key(group_privkey, name)

            genesis = events.create_group_genesis(
                group_privkey, self.user_pubkey, name, description, public, relay_urls
            )

            dag_obj = self.storage.get_group_dag(group_pubkey)
            lock = self._worker.get_lock(group_pubkey)
            with lock:
                dag_obj.add_event(genesis, skip_verify=True, skip_auth=True)
            join_event = events.create_group_join(
                group_pubkey, self.user_pubkey, self.user_privkey, [genesis["id"]]
            )
            with lock:
                dag_obj.add_event(join_event, skip_verify=True, skip_auth=True)

            self._pending_publishes[genesis["id"]] = {
                "group_pubkey": group_pubkey,
                "event": genesis,
            }
            self._pending_publishes[join_event["id"]] = {
                "group_pubkey": group_pubkey,
                "event": join_event,
            }
            self._worker.publish_event(genesis, relay_urls)
            self._worker.publish_event(join_event, relay_urls)

            self._refresh_group_cache(group_pubkey)
            self.state_changed.emit(group_pubkey)
            self.group_created.emit(group_pubkey)
            self._log("info", f"Group '{name}' created")
        except Exception as e:
            self._log("error", f"Failed to create group: {e}")

    def join_group(self, address: str):
        """Join a group by address (pubkey@relay1,relay2)."""
        if "@" not in address:
            self._log("warning", "Invalid group address format")
            return

        pubkey_part, relays_part = address.split("@", 1)
        group_pubkey = pubkey_part.strip()
        hint_relays = [r.strip() for r in relays_part.split(",") if r.strip()]

        if not group_pubkey or len(group_pubkey) < 10:
            self._log("warning", "Invalid group public key")
            return

        try:
            dag_obj = self.storage.get_group_dag(group_pubkey)
            lock = self._worker.get_lock(group_pubkey)
            with lock:
                state = dag_obj.get_state()

            if self.user_pubkey in state.joined:
                self.group_joined.emit(group_pubkey)
                return

            self._pending_actions[group_pubkey] = ("join_after_sync", hint_relays)
            self.sync_group(group_pubkey, hint_relays)
            self._log("info", "Joining group...")
        except Exception as e:
            self._log("error", f"Failed to join group: {e}")
            self._pending_actions.pop(group_pubkey, None)

    def leave_group(self, group_pubkey: str):
        """Leave a group."""

        def check(state):
            if self.user_pubkey not in state.joined:
                raise PermissionError("Not a member of this group")
            if self.user_pubkey == state.genesis["content"]["founder"]:
                raise PermissionError("Founder cannot leave their own group")

        def create(tips):
            return events.create_group_leave(
                group_pubkey, self.user_pubkey, self.user_privkey, tips
            )

        def post(event):
            self.group_left.emit(group_pubkey)

        self._publish_action(group_pubkey, create, check, post)

    def subscribe_group(self, group_pubkey: str):
        """Start live subscriptions for a group."""
        dag_obj = self.storage.get_group_dag(group_pubkey)
        lock = self._worker.get_lock(group_pubkey)
        with lock:
            state = dag_obj.get_state()
        relay_urls = state.relays if state.relays else config.BOOTSTRAP_RELAYS
        self._worker.start_subscriptions(group_pubkey, relay_urls)
        self._log("info", "Subscribed to group events")

    def unsubscribe_group(self, group_pubkey: str):
        """Stop subscriptions for a group."""
        self._worker.stop_subscriptions(group_pubkey)
        self._log("info", "Unsubscribed from group events")

    def invite_user(self, group_pubkey: str, invitee_pubkey: str):
        """Invite a user (mod only)."""

        def check(state):
            if self.user_pubkey not in state.mods:
                raise PermissionError("Only moderators can invite users")

        def create(tips):
            return events.create_group_invite(
                group_pubkey, self.user_pubkey, self.user_privkey, invitee_pubkey, tips
            )

        self._publish_action(group_pubkey, create, check)

    def kick_user(self, group_pubkey: str, target_pubkey: str):
        """Kick a user (mod only)."""

        def check(state):
            if self.user_pubkey not in state.mods:
                raise PermissionError("Only moderators can kick users")

        def create(tips):
            return events.create_group_kick(
                group_pubkey, self.user_pubkey, self.user_privkey, target_pubkey, tips
            )

        self._publish_action(group_pubkey, create, check)

    def promote_mod(self, group_pubkey: str, target_pubkey: str):
        """Promote to mod (mod only)."""

        def check(state):
            if self.user_pubkey not in state.mods:
                raise PermissionError("Only moderators can promote users")

        def create(tips):
            return events.create_mod_add(
                group_pubkey, self.user_pubkey, self.user_privkey, target_pubkey, tips
            )

        self._publish_action(group_pubkey, create, check)

    def demote_mod(self, group_pubkey: str, target_pubkey: str):
        """Demote from mod (mod only)."""

        def check(state):
            if self.user_pubkey not in state.mods:
                raise PermissionError("Only moderators can demote users")

        def create(tips):
            return events.create_mod_remove(
                group_pubkey, self.user_pubkey, self.user_privkey, target_pubkey, tips
            )

        self._publish_action(group_pubkey, create, check)
