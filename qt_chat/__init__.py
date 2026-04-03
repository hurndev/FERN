"""qt_chat package — PyQt5 FERN chat application entry point."""

import sys
import signal

from PyQt5.QtWidgets import QApplication, QDialog

from .app import FernChatMain
from .styles import RETRO_STYLESHEET
from .dialogs import IdentityDialog


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(RETRO_STYLESHEET)

    def sigint_handler(*args):
        controller.shutdown()
        app.quit()

    signal.signal(signal.SIGINT, sigint_handler)

    from .controller import ChatController
    from fern.storage import resolve_fern_dir

    fern_dir = resolve_fern_dir()
    print(f"[FERN] Storage directory: {fern_dir}")

    controller = ChatController(str(fern_dir.parent))

    print("[FERN] Loading identity from disk...")
    if not controller.load_identity():
        print("[FERN] No identity found — creating new one")
        dialog = IdentityDialog(controller, parent=None)
        if dialog.exec_() != QDialog.Accepted:
            sys.exit(0)
    else:
        pubkey, _ = controller.get_identity()
        print(f"[FERN] Identity loaded: {pubkey[:16]}...")

    print("[FERN] Loading groups from disk...")
    controller._populate_group_cache()
    groups = controller.list_groups()
    print(f"[FERN] Found {len(groups)} group(s) locally")
    for g in groups:
        print(
            f"       - {g['name']}  pubkey={g['pubkey'][:16]}...  "
            f"events={g['event_count']}  members={g['member_count']}  "
            f"relays={len(g['relays'])}  joined={g['joined']}"
        )

    window = FernChatMain(controller)
    window.show()

    app.aboutToQuit.connect(controller.shutdown)

    sys.exit(app.exec_())
