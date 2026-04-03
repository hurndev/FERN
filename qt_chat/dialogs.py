"""Dialog classes for the Qt chat app."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFormLayout,
    QComboBox,
    QMessageBox,
    QApplication,
)

from .widgets import RetroTitleBar, ClickableLabel, short_key, get_role


class IdentityDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("FERN — Identity")
        self.setFixedSize(420, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("FERN Chat")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #006600; "
            "font-family: 'MS Sans Serif', sans-serif; padding: 8px;"
        )
        layout.addWidget(title)

        subtitle = QLabel("Fault-tolerant Event Relay Network")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(
            "font-size: 10px; color: #666; font-style: italic; padding: 2px;"
        )
        layout.addWidget(subtitle)

        from PyQt5.QtWidgets import QFrame

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        info = QLabel(
            "FERN has no accounts or passwords.\n"
            "Your identity is an Ed25519 keypair stored locally."
        )
        info.setAlignment(Qt.AlignCenter)
        info.setStyleSheet("font-size: 11px; color: #333; padding: 4px;")
        layout.addWidget(info)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Display name (optional)")
        self.name_input.setStyleSheet("padding: 4px;")
        layout.addWidget(self.name_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        gen_btn = QPushButton("Generate New Identity")
        gen_btn.setObjectName("SendButton")
        gen_btn.clicked.connect(self._generate)
        btn_row.addWidget(gen_btn)

        import_btn = QPushButton("Import Key")
        import_btn.clicked.connect(self._import)
        btn_row.addWidget(import_btn)

        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 10px; color: #666;")
        layout.addWidget(self.status_label)

    def _generate(self):
        self.controller.generate_identity()
        pubkey, _ = self.controller.get_identity()
        self.status_label.setText(f"Generated: {short_key(pubkey)}")
        self.accept()

    def _import(self):
        from PyQt5.QtWidgets import QInputDialog

        key, ok = QInputDialog.getText(
            self, "Import Identity", "Paste your private key (hex):"
        )
        if ok and key.strip():
            priv = key.strip()
            try:
                pubkey, _ = self.controller.import_identity(priv)
                self.status_label.setText(f"Imported: {short_key(pubkey)}")
                self.accept()
            except Exception as e:
                self.status_label.setText(f"Import failed: {e}")


class CreateGroupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.group_name = ""
        self.group_desc = ""
        self.is_public = True
        self.relays = []
        self.setWindowTitle("Create Group")
        self.setFixedSize(440, 340)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        header = RetroTitleBar("Create New Group", "#005500", "#00AA44")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(6)

        self.name_input = QLineEdit("My Group")
        form.addRow("Name:", self.name_input)

        self.desc_input = QLineEdit("A FERN group chat")
        form.addRow("Description:", self.desc_input)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["Public (anyone can join)", "Private (invite only)"])
        form.addRow("Type:", self.type_combo)

        self.relays_input = QLineEdit("ws://localhost:8787, ws://localhost:8788")
        self.relays_input.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 10px;"
        )
        form.addRow("Relays:", self.relays_input)

        layout.addLayout(form)

        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 9px; color: #666; "
            "background-color: #F0F0F0; border: 1px solid #CCC; padding: 4px;"
        )
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        create_btn = QPushButton("Create")
        create_btn.setObjectName("SendButton")
        create_btn.clicked.connect(self._create)
        btn_row.addWidget(create_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _validate_relay_url(self, url: str) -> bool:
        return url.startswith("ws://") or url.startswith("wss://")

    def _create(self):
        name = self.name_input.text().strip() or "Unnamed Group"
        desc = self.desc_input.text().strip() or ""
        is_public = self.type_combo.currentIndex() == 0
        relays_raw = self.relays_input.text().strip()
        relays = [r.strip() for r in relays_raw.split(",") if r.strip()]

        if not relays:
            QMessageBox.warning(self, "Error", "At least one relay URL is required.")
            return

        invalid = [r for r in relays if not self._validate_relay_url(r)]
        if invalid:
            QMessageBox.warning(
                self,
                "Error",
                f"Invalid relay URL(s): {', '.join(invalid)}\nUse ws:// or wss:// prefix.",
            )
            return

        self.group_name = name
        self.group_desc = desc
        self.is_public = is_public
        self.relays = relays
        self.accept()


class JoinGroupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.address = ""
        self.setWindowTitle("Join Group")
        self.setFixedSize(460, 220)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        header = RetroTitleBar("Join Group", "#000080", "#1084D0")
        layout.addWidget(header)

        info = QLabel(
            "Paste a group address in the format:\ngroup_pubkey@relay1,relay2,relay3"
        )
        info.setStyleSheet("font-size: 10px; color: #333; padding: 4px;")
        layout.addWidget(info)

        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText(
            "a3f8b2c1...@ws://relay1.example.com:8787,ws://relay2.example.com:8788"
        )
        self.address_input.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 10px; padding: 4px;"
        )
        layout.addWidget(self.address_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        join_btn = QPushButton("Join")
        join_btn.setObjectName("SendButton")
        join_btn.clicked.connect(self._join)
        btn_row.addWidget(join_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _join(self):
        address = self.address_input.text().strip()
        if not address or "@" not in address:
            QMessageBox.warning(
                self, "Error", "Invalid group address. Format: pubkey@relay1,relay2"
            )
            return

        pubkey_part, relays_part = address.split("@", 1)
        pubkey = pubkey_part.strip()
        relays = [r.strip() for r in relays_part.split(",") if r.strip()]

        if not pubkey or len(pubkey) < 10:
            QMessageBox.warning(self, "Error", "Invalid group public key.")
            return

        if not relays:
            QMessageBox.warning(self, "Error", "No relay URLs found in address.")
            return

        invalid = [
            r for r in relays if not (r.startswith("ws://") or r.startswith("wss://"))
        ]
        if invalid:
            QMessageBox.warning(
                self,
                "Error",
                f"Invalid relay URL(s): {', '.join(invalid)}\nUse ws:// or wss:// prefix.",
            )
            return

        self.address = address
        self.accept()


class UserProfileDialog(QDialog):
    def __init__(
        self, pubkey: str, state, user_pubkey: str, group_events: list, parent=None
    ):
        super().__init__(parent)
        self.pubkey = pubkey
        self.state = state
        self.user_pubkey = user_pubkey
        self.group_events = group_events
        self.setWindowTitle(
            "User Profile (You)" if pubkey == user_pubkey else "User Profile"
        )
        self.setFixedSize(380, 280)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        is_self = self.pubkey == self.user_pubkey
        header_title = "User Profile (You)" if is_self else "User Profile"
        header = RetroTitleBar(header_title, "#000080", "#1084D0")
        layout.addWidget(header)

        founder = (
            self.state.genesis["content"].get("founder", "")
            if self.state and self.state.genesis
            else ""
        )
        role = get_role(self.pubkey, self.state)
        is_joined = self.pubkey in (self.state.joined if self.state else set())
        is_founder = self.pubkey == founder

        role_icon = "👑" if is_founder else ("🛡️" if role == "mod" else "👤")
        role_label = (
            "Founder" if is_founder else ("Moderator" if role == "mod" else "Member")
        )

        pubkey_row = QHBoxLayout()
        pubkey_row.addWidget(QLabel("ID: "))
        pubkey_value_label = ClickableLabel(
            f"{self.pubkey}  (click to copy)",
            lambda: QApplication.clipboard().setText(self.pubkey),
        )
        self._pubkey_label = pubkey_value_label
        pubkey_row.addWidget(pubkey_value_label, 1)
        pubkey_row.addStretch()
        layout.addLayout(pubkey_row)

        info_lines = [
            f"Role: {role_icon} {role_label}",
            f"Status: {'Joined' if is_joined else 'Not joined'}",
        ]
        if self.state:
            info_lines.append(f"Group: {self.state.metadata.get('name', 'Unknown')}")

        info_text = "\n".join(info_lines)
        info_label = QLabel(info_text)
        info_label.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 11px; "
            "background-color: #F0F0F0; border: 1px solid #CCC; padding: 8px;"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        msg_count = sum(
            1
            for e in self.group_events
            if e.get("type") == "message" and e.get("author") == self.pubkey
        )
        msg_label = QLabel(f"Messages in this group: {msg_count}")
        msg_label.setStyleSheet("font-size: 10px; color: #666;")
        layout.addWidget(msg_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
