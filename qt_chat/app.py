"""FernChatMain window, all widget/dialog classes, stylesheet, helpers.

All visual code lives here. Does no protocol I/O directly — all data comes
from ChatController via Qt signals.
"""

import sys
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QPushButton,
    QLabel,
    QFrame,
    QComboBox,
    QAction,
    QMenuBar,
    QMenu,
    QTabWidget,
    QStatusBar,
    QInputDialog,
    QMessageBox,
    QLineEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
)
from PyQt5.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QFont,
    QColor,
    QPainter,
    QPen,
    QBrush,
    QLinearGradient,
    QTextCursor,
    QTextCharFormat,
    QFontMetrics,
)


RETRO_STYLESHEET = """
    QMainWindow {
        background-color: #C0C0C0;
    }
    QMainWindow#FernChatMain {
        background-color: #D4D0C8;
    }
    QMenuBar {
        background-color: #D4D0C8;
        border-bottom: 1px solid #808080;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
    }
    QMenuBar::item {
        padding: 2px 8px;
    }
    QMenuBar::item:selected {
        background-color: #000080;
        color: white;
    }
    QMenu {
        background-color: #D4D0C8;
        border: 1px solid #808080;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
    }
    QMenu::item:selected {
        background-color: #000080;
        color: white;
    }
    QToolBar {
        background-color: #D4D0C8;
        border-bottom: 1px solid #808080;
        spacing: 2px;
        padding: 2px;
    }
    QListWidget {
        background-color: #FFFFFF;
        border: 2px inset #808080;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
        outline: none;
    }
    QListWidget::item {
        padding: 3px 4px;
        border-bottom: none;
    }
    QListWidget::item:selected {
        background-color: #000080;
        color: #FFFFFF;
    }
    QHeaderView::section {
        background-color: #D4D0C8;
        border: 1px solid #808080;
        padding: 3px;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
        font-weight: bold;
    }
    QTextEdit {
        background-color: #FFFFFF;
        border: 2px inset #808080;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 12px;
    }
    QTextEdit#MessageInput {
        background-color: #FFFFFF;
        border: 2px inset #808080;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 12px;
    }
    QTextEdit#EventLog {
        background-color: #F8F8F8;
        border: 2px inset #808080;
        font-family: 'Courier New', monospace;
        font-size: 10px;
    }
    QPushButton {
        background-color: #D4D0C8;
        border: 2px outset #FFFFFF;
        border-right-color: #808080;
        border-bottom-color: #808080;
        padding: 4px 12px;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
        min-height: 21px;
    }
    QPushButton:hover {
        background-color: #E0E0E0;
    }
    QPushButton:pressed {
        border: 2px inset #808080;
        background-color: #C0C0C0;
    }
    QPushButton#SendButton {
        background-color: #000080;
        color: white;
        font-weight: bold;
        border: 2px outset #4040C0;
        padding: 4px 16px;
    }
    QPushButton#SendButton:hover {
        background-color: #0000A0;
    }
    QPushButton#SendButton:pressed {
        background-color: #000060;
        border: 2px inset #4040C0;
    }
    QComboBox {
        background-color: #D4D0C8;
        border: 2px inset #808080;
        padding: 2px 4px;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
        min-height: 18px;
    }
    QComboBox::drop-down {
        border: 1px outset #FFFFFF;
        border-right-color: #808080;
        border-bottom-color: #808080;
        width: 16px;
    }
    QTabWidget::pane {
        border: 2px inset #808080;
        background-color: #FFFFFF;
    }
    QTabBar::tab {
        background-color: #D4D0C8;
        border: 1px solid #808080;
        padding: 4px 10px;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
    }
    QTabBar::tab:selected {
        background-color: #FFFFFF;
        border-bottom: none;
    }
    QStatusBar {
        background-color: #D4D0C8;
        border-top: 1px solid #808080;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 10px;
    }
    QScrollBar:vertical {
        background-color: #D4D0C8;
        width: 16px;
        border: 1px solid #808080;
    }
    QScrollBar::handle:vertical {
        background-color: #C0C0C0;
        border: 1px outset #FFFFFF;
        border-right-color: #808080;
        border-bottom-color: #808080;
        min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 16px;
        background-color: #D4D0C8;
        border: 1px outset #FFFFFF;
        border-right-color: #808080;
        border-bottom-color: #808080;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background-color: #D4D0C8;
    }
    QLabel {
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
    }
    QSplitter::handle {
        background-color: #D4D0C8;
    }
    QGroupBox {
        border: 2px groove #FFFFFF;
        margin-top: 4px;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
        font-weight: bold;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 3px;
    }
    QProgressBar {
        border: 2px inset #808080;
        background-color: #D4D0C8;
        text-align: center;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 9px;
        height: 14px;
    }
    QProgressBar::chunk {
        background-color: #008000;
    }
    QLineEdit {
        background-color: #FFFFFF;
        border: 2px inset #808080;
        padding: 3px;
        font-family: 'MS Sans Serif', 'Tahoma', 'Arial', sans-serif;
        font-size: 11px;
    }
"""


RELAY_COLORS = [
    ("#008000", "#00AA00"),
    ("#800000", "#AA0000"),
    ("#000080", "#0000AA"),
    ("#808000", "#AAAA00"),
    ("#800080", "#AA00AA"),
    ("#008080", "#00AAAA"),
]


def short_key(key: str, chars: int = 10) -> str:
    if not key:
        return "???"
    return key[:chars] + "..."


def get_role(pubkey: str, state) -> str:
    """Determine user role from group state."""
    if state and state.genesis:
        founder = state.genesis["content"].get("founder", "")
        if pubkey == founder:
            return "founder"
    if state and pubkey in state.mods:
        return "mod"
    return "member"


def format_timestamp(ts: int) -> str:
    """Format unix timestamp for display."""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class RetroTitleBar(QFrame):
    def __init__(self, title, color_top="#000080", color_bottom="#1084D0", parent=None):
        super().__init__(parent)
        self.title = title
        self.color_top = QColor(color_top)
        self.color_bottom = QColor(color_bottom)
        self.setFixedHeight(28)
        self.setStyleSheet("border: none;")

    def set_title(self, title: str):
        self.title = title
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, self.color_top)
        gradient.setColorAt(1, self.color_bottom)
        painter.fillRect(self.rect(), QBrush(gradient))
        painter.setPen(QPen(QColor(255, 255, 255)))
        font = QFont("MS Sans Serif", 9, QFont.Bold)
        painter.setFont(font)
        painter.drawText(
            self.rect().adjusted(6, 0, -6, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.title,
        )
        painter.end()


class RelayStatusBar(QFrame):
    def __init__(self, relay_urls: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.relay_urls = list(relay_urls) if relay_urls else []
        self._statuses: dict[str, str] = {r: "disconnected" for r in self.relay_urls}
        self.setFixedHeight(22)
        self.setStyleSheet("background-color: #D4D0C8; border: 1px solid #808080;")
        self.relay_colors = {}
        for i, r in enumerate(self.relay_urls):
            self.relay_colors[r] = RELAY_COLORS[i % len(RELAY_COLORS)]

    def paintEvent(self, event):
        painter = QPainter(self)
        x = 6
        for relay in self.relay_urls:
            color_on = self.relay_colors.get(relay, ("#008000", "#00AA00"))[0]
            status = self._statuses.get(relay, "disconnected")
            if status == "connected":
                brush_color = QColor(color_on)
            else:
                brush_color = QColor("#FF0000")
            painter.setBrush(QBrush(brush_color))
            painter.setPen(QPen(QColor("#333333")))
            painter.drawEllipse(x, 5, 10, 10)
            painter.setPen(QPen(QColor("#333333")))
            font = QFont("MS Sans Serif", 8)
            painter.setFont(font)
            short_name = relay.replace("wss://", "").replace("ws://", "")
            painter.drawText(x + 14, 13, short_name)
            x += painter.fontMetrics().horizontalAdvance(short_name) + 24
        painter.end()

    def set_relay_status(self, relay: str, status: str):
        self._statuses[relay] = status
        self.update()

    def set_relays(self, relay_urls: list[str]):
        """Update the relay list dynamically."""
        self.relay_urls = list(relay_urls)
        self._statuses = {r: self._statuses.get(r, "disconnected") for r in relay_urls}
        for i, r in enumerate(relay_urls):
            if r not in self.relay_colors:
                self.relay_colors[r] = RELAY_COLORS[i % len(RELAY_COLORS)]
        self.update()


class GroupListItem(QListWidgetItem):
    def __init__(self, group_info: dict, is_joined: bool = False, parent=None):
        super().__init__(parent)
        self.group_info = group_info
        self.is_joined = is_joined
        self._update_text()

    def _update_text(self):
        icon = "✓" if self.is_joined else "○"
        pub = short_key(self.group_info.get("pubkey", ""), 6)
        name = self.group_info.get("name", "Unknown")
        count = self.group_info.get("member_count", 0)
        self.setText(f"{icon} {name} ({count}) [{pub}]")


class MemberItemWidget(QWidget):
    member_clicked = pyqtSignal(str)

    def __init__(self, pubkey: str, state, user_pubkey: str, parent=None):
        super().__init__(parent)
        self.pubkey = pubkey
        self.state = state
        self.user_pubkey = user_pubkey
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        founder = (
            self.state.genesis["content"].get("founder", "")
            if self.state and self.state.genesis
            else ""
        )
        role = get_role(self.pubkey, self.state)
        is_joined = self.pubkey in (self.state.joined if self.state else set())
        is_self = self.pubkey == self.user_pubkey
        is_founder = self.pubkey == founder

        role_icon = "👑" if is_founder else ("🛡️" if role == "mod" else "👤")

        self.icon_label = QLabel(role_icon)
        self.icon_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.icon_label)

        self.name_label = QLabel(short_key(self.pubkey))
        self.name_label.setStyleSheet(
            "font-size: 11px; color: #0000AA; text-decoration: underline;"
            "font-family: 'MS Sans Serif', sans-serif;"
        )
        self.name_label.setCursor(Qt.PointingHandCursor)
        self.name_label.mousePressEvent = lambda e: self.member_clicked.emit(
            self.pubkey
        )
        layout.addWidget(self.name_label, 1)

        if is_self:
            self_tag = QLabel("(you)")
            self_tag.setStyleSheet("font-size: 9px; color: #666; font-style: italic;")
            layout.addWidget(self_tag)

        if is_founder:
            self.name_label.setStyleSheet(
                self.name_label.styleSheet().replace("#0000AA", "#8B0000")
            )
            font = self.name_label.font()
            font.setBold(True)
            self.name_label.setFont(font)
        elif role == "mod":
            self.name_label.setStyleSheet(
                self.name_label.styleSheet().replace("#0000AA", "#000080")
            )

        if not is_joined:
            self.name_label.setStyleSheet(
                "font-size: 11px; color: #888; text-decoration: underline;"
                "font-family: 'MS Sans Serif', sans-serif; font-style: italic;"
            )
            self.icon_label.setStyleSheet("font-size: 12px; opacity: 0.5;")


class ClickableLabel(QLabel):
    def __init__(self, text, callback, parent=None):
        super().__init__(text, parent)
        self.callback = callback
        self.original_text = text
        self.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 11px; "
            "color: #0000EE; text-decoration: underline;"
        )
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        self.callback()
        self.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 11px; color: #551A8B;"
        )
        QTimer.singleShot(1500, self._restore)

    def _restore(self):
        self.setStyleSheet(
            "font-family: 'Courier New', monospace; font-size: 11px; "
            "color: #0000EE; text-decoration: underline;"
        )


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


class GroupChatView(QWidget):
    pubkey_clicked = pyqtSignal(str)

    def __init__(self, group_pubkey: str, controller, parent=None):
        super().__init__(parent)
        self.group_pubkey = group_pubkey
        self.controller = controller
        self._last_event_id: str | None = None
        self._displayed_ids: set[str] = set()
        self._setup_ui()
        self._populate_events()
        self.chat_display.anchorClicked.connect(self._on_anchor_clicked)

    def _setup_ui(self, layout=None):
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)

        state = self.controller.get_group_state(self.group_pubkey)
        name = (
            state.metadata.get("name", f"Group_{self.group_pubkey[:8]}")
            if state
            else "Loading..."
        )
        member_count = len(state.joined) if state else 0
        relay_count = len(state.relays) if state else 0

        header = RetroTitleBar(
            f"  {name}  |  {member_count} members  |  {relay_count} relays  ",
            "#005500",
            "#00AA44",
        )
        layout.addWidget(header)
        self._header = header

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)

        desc = state.metadata.get("description", "") if state else ""
        desc_label = QLabel(f'"{desc}"')
        desc_label.setStyleSheet("font-size: 9px; color: #444; font-style: italic;")
        meta_row.addWidget(desc_label)

        sep = QLabel(" | ")
        sep.setStyleSheet("font-size: 9px; color: #888;")
        meta_row.addWidget(sep)

        relay_str = ",".join(state.relays) if state and state.relays else ""
        self._full_address = (
            f"{self.group_pubkey}@{relay_str}" if relay_str else self.group_pubkey
        )
        short_pub = f"{self.group_pubkey[:8]}...{self.group_pubkey[-8:]}"
        display_address = f"{short_pub}@{relay_str}" if relay_str else short_pub
        self.address_label = QLabel(display_address)
        self.address_label.setStyleSheet(
            "font-size: 9px; color: #0000AA; text-decoration: underline; "
            "font-family: 'Courier New', monospace;"
        )
        self.address_label.setCursor(Qt.PointingHandCursor)
        self.address_label.setToolTip("Click to copy group address")
        self.address_label.installEventFilter(self)
        meta_row.addWidget(self.address_label)

        meta_row.addStretch()

        user_pubkey, _ = self.controller.get_identity()
        is_joined = state and self.controller.is_joined(self.group_pubkey)
        if not is_joined:
            self.join_btn = QPushButton("Join")
            self.join_btn.setFixedWidth(50)
            self.join_btn.setStyleSheet(
                "QPushButton { background-color: #000080; color: white; "
                "font-weight: bold; border: 2px outset #4040C0; padding: 2px 8px; "
                "font-size: 10px; min-height: 18px; }"
                "QPushButton:hover { background-color: #0000A0; }"
                "QPushButton:pressed { border: 2px inset #4040C0; background-color: #000060; }"
            )
            self.join_btn.clicked.connect(self._on_join_clicked)
            meta_row.addWidget(self.join_btn)

        layout.addLayout(meta_row)

        relay_urls = state.relays if state and state.relays else []
        self.relay_bar = RelayStatusBar(relay_urls)
        layout.addWidget(self.relay_bar)

        from PyQt5.QtWidgets import QTextBrowser, QSplitter

        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setOpenLinks(False)

        input_widget = QWidget()
        input_row = QHBoxLayout(input_widget)
        input_row.setContentsMargins(4, 4, 4, 4)
        input_row.setSpacing(4)

        self.message_input = QTextEdit()
        self.message_input.setObjectName("MessageInput")
        self.message_input.setMinimumHeight(50)
        self.message_input.setMaximumHeight(200)
        self.message_input.setPlaceholderText("Type a message to the group...")
        self.message_input.installEventFilter(self)
        input_row.addWidget(self.message_input, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(2)

        send_btn = QPushButton("Send")
        send_btn.setObjectName("SendButton")
        send_btn.setFixedHeight(50)
        send_btn.clicked.connect(self._send_message)
        btn_col.addWidget(send_btn)

        input_row.addLayout(btn_col)

        splitter = QSplitter(Qt.Vertical)
        self._chat_splitter = QSplitter(Qt.Vertical)
        self._chat_splitter.addWidget(self.chat_display)
        self._chat_splitter.addWidget(input_widget)
        self._chat_splitter.setStretchFactor(0, 1)
        self._chat_splitter.setStretchFactor(1, 0)
        self._chat_splitter.setSizes([1000, 50])
        self._chat_splitter.splitterMoved.connect(self._enforce_input_max_height)
        layout.addWidget(self._chat_splitter, 1)

    def _enforce_input_max_height(self, pos, index):
        if index == 1:
            sizes = self._chat_splitter.sizes()
            if sizes[1] > 210:
                sizes[1] = 210
                self._chat_splitter.setSizes(sizes)

    def _on_anchor_clicked(self, url):
        if url.scheme() == "pubkey":
            self.pubkey_clicked.emit(url.path().lstrip("/"))

    def _ts_str(self, ts):
        return format_timestamp(ts)

    def _append_system(self, text, color="#000080"):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setFont(QFont("MS Sans Serif", 9, QFont.Normal, True))
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(f"*** {text} ***\n")
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _insert_pubkey_link(self, cursor, pubkey, color="#0000AA"):
        link_fmt = QTextCharFormat()
        link_fmt.setFont(QFont("MS Sans Serif", 10, QFont.Bold))
        link_fmt.setForeground(QColor(color))
        link_fmt.setAnchor(True)
        link_fmt.setAnchorHref(f"pubkey:/{pubkey}")
        link_fmt.setToolTip(f"View profile: {short_key(pubkey)}")
        link_fmt.setFontUnderline(True)
        cursor.setCharFormat(link_fmt)
        cursor.insertText(short_key(pubkey))
        return link_fmt

    def _append_event(self, event):
        etype = event.get("type", "unknown")
        author = event.get("author", "???")
        ts = event.get("ts", 0)
        content = event.get("content", "")
        event_id = event.get("id", "???")

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)

        ts_str = self._ts_str(ts)

        user_pubkey, _ = self.controller.get_identity()
        state = self.controller.get_group_state(self.group_pubkey)
        founder = (
            state.genesis["content"].get("founder", "")
            if state and state.genesis
            else ""
        )
        role = get_role(author, state)

        event_display = ""
        color = "#666666"

        if etype == "group_genesis":
            name = content.get("name", "???") if isinstance(content, dict) else "???"
            event_display = f'Group created by {short_key(author)} — "{name}"'
            color = "#006600"
        elif etype == "group_join":
            event_display = f"{short_key(author)} joined the group"
            color = "#006600"
        elif etype == "group_leave":
            event_display = f"{short_key(author)} left the group"
            color = "#CC6600"
        elif etype == "group_kick":
            target = (
                content.get("target", "???") if isinstance(content, dict) else "???"
            )
            event_display = f"{short_key(author)} kicked {short_key(target)}"
            color = "#CC0000"
        elif etype == "group_invite":
            target = (
                content.get("invitee", "???") if isinstance(content, dict) else "???"
            )
            event_display = f"{short_key(author)} invited {short_key(target)}"
            color = "#0066CC"
        elif etype == "mod_add":
            target = (
                content.get("target", "???") if isinstance(content, dict) else "???"
            )
            event_display = f"{short_key(author)} promoted {short_key(target)} to mod"
            color = "#800080"
        elif etype == "mod_remove":
            target = (
                content.get("target", "???") if isinstance(content, dict) else "???"
            )
            event_display = f"{short_key(author)} demoted {short_key(target)}"
            color = "#806600"
        elif etype == "relay_update":
            relays = content.get("relays", []) if isinstance(content, dict) else []
            event_display = f"Relay list updated: {', '.join(r.replace('wss://', '').replace('ws://', '') for r in relays)}"
            color = "#006666"
        elif etype == "group_metadata":
            new_name = content.get("name", "") if isinstance(content, dict) else ""
            new_desc = (
                content.get("description", "") if isinstance(content, dict) else ""
            )
            event_display = f'Group updated: name="{new_name}" desc="{new_desc}"'
            color = "#006666"
        elif etype == "message":
            role_tag = ""
            if role == "founder":
                role_tag = " [FOUNDER]"
                color = "#8B0000"
            elif role == "mod":
                role_tag = " [MOD]"
                color = "#000080"
            else:
                color = "#004400"

            display_name = short_key(author)
            is_self = author == user_pubkey
            if is_self:
                display_name = f"You"
                color = "#0000AA"

            self._insert_pubkey_link(cursor, author, color)

            role_fmt = QTextCharFormat()
            role_fmt.setFont(QFont("MS Sans Serif", 10, QFont.Bold))
            role_fmt.setForeground(QColor(color))
            cursor.setCharFormat(role_fmt)
            cursor.insertText(f"{role_tag} ")

            ts_fmt = QTextCharFormat()
            ts_fmt.setFont(QFont("MS Sans Serif", 9))
            ts_fmt.setForeground(QColor("#666666"))
            cursor.setCharFormat(ts_fmt)
            cursor.insertText(f"[{ts_str}]: ")

            msg_fmt = QTextCharFormat()
            msg_fmt.setFont(QFont("MS Sans Serif", 10))
            msg_fmt.setForeground(QColor("#000000"))
            cursor.setCharFormat(msg_fmt)
            cursor.insertText(f"{content}\n")

            self.chat_display.setTextCursor(cursor)
            self.chat_display.ensureCursorVisible()
            return
        else:
            event_display = f"[{etype}] {short_key(author)}"

        fmt = QTextCharFormat()
        fmt.setFont(QFont("MS Sans Serif", 9, QFont.Normal, True))
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(f"[{ts_str}] {event_display}\n")

        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _populate_events(self):
        events = self.controller.get_group_events(self.group_pubkey)
        if not events:
            self._append_system("No events yet. The group is empty.")
            return

        sorted_events = sorted(events, key=lambda e: (e.get("ts", 0), e.get("id", "")))
        for event in sorted_events:
            self._append_event(event)
            self._displayed_ids.add(event.get("id"))
            self._last_event_id = event.get("id")

        self.chat_display.moveCursor(QTextCursor.End)

    def _append_new_events(self):
        events = self.controller.get_group_events(self.group_pubkey)
        if not events:
            return

        sorted_events = sorted(events, key=lambda e: (e.get("ts", 0), e.get("id", "")))
        new_events = [
            e for e in sorted_events if e.get("id") not in self._displayed_ids
        ]

        for event in new_events:
            self._append_event(event)
            self._displayed_ids.add(event.get("id"))
            self._last_event_id = event.get("id")

        if new_events:
            self.chat_display.moveCursor(QTextCursor.End)

    def _copy_group_address(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._full_address)
        relay_str = (
            ",".join(self.controller.get_group_state(self.group_pubkey).relays)
            if self.controller.get_group_state(self.group_pubkey)
            else ""
        )
        short_pub = f"{self.group_pubkey[:8]}...{self.group_pubkey[-8:]}"
        display_address = f"{short_pub}@{relay_str}" if relay_str else short_pub
        original_style = self.address_label.styleSheet()
        self.address_label.setStyleSheet(
            "font-size: 9px; color: #008800; text-decoration: none; "
            "font-family: 'Courier New', monospace;"
        )
        self.address_label.setText("Copied to clipboard!")
        QTimer.singleShot(
            1500,
            lambda: (
                self.address_label.setStyleSheet(original_style),
                self.address_label.setText(display_address),
            ),
        )

    def _on_join_clicked(self):
        state = self.controller.get_group_state(self.group_pubkey)
        relay_str = ",".join(state.relays) if state and state.relays else ""
        address = f"{self.group_pubkey}@{relay_str}" if relay_str else self.group_pubkey
        self.controller.join_group(address)

    def _send_message(self):
        text = self.message_input.toPlainText().strip()
        if not text:
            return
        self.controller.send_message(self.group_pubkey, text)
        self.message_input.clear()

    def eventFilter(self, obj, event):
        if event.type() == event.KeyPress:
            if hasattr(self, "message_input") and obj == self.message_input:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    if event.modifiers() & Qt.ShiftModifier:
                        return False
                    self._send_message()
                    return True
        if hasattr(self, "address_label") and obj == self.address_label:
            if event.type() == event.MouseButtonPress:
                self._copy_group_address()
                return True
        return super().eventFilter(obj, event)

    def refresh_header(self):
        state = self.controller.get_group_state(self.group_pubkey)
        if not state:
            return
        name = state.metadata.get("name", f"Group_{self.group_pubkey[:8]}")
        member_count = len(state.joined)
        relay_count = len(state.relays)
        self._header.set_title(
            f"  {name}  |  {member_count} members  |  {relay_count} relays  "
        )
        relay_str = ",".join(state.relays) if state.relays else ""
        self._full_address = (
            f"{self.group_pubkey}@{relay_str}" if relay_str else self.group_pubkey
        )
        short_pub = f"{self.group_pubkey[:8]}...{self.group_pubkey[-8:]}"
        self.address_label.setText(
            f"{short_pub}@{relay_str}" if relay_str else short_pub
        )
        is_public = state.public
        layout = self.layout()
        if layout:
            meta_row = layout.itemAt(1)
            if meta_row and meta_row.layout():
                for i in range(meta_row.layout().count()):
                    w = meta_row.layout().itemAt(i).widget()
                    if isinstance(w, QLabel):
                        style = w.styleSheet()
                        if "font-weight: bold" in style:
                            w.setText("Public" if is_public else "Private")
                        elif "font-style: italic" in style:
                            w.setText(f'"{state.metadata.get("description", "")}"')


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


class FernChatMain(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.setObjectName("FernChatMain")
        self.setWindowTitle("FERN Chat — Fault-tolerant Event Relay Network")
        self.setMinimumSize(900, 600)
        self.resize(1050, 650)

        self.controller = controller
        self.chat_views: dict[str, GroupChatView] = {}
        self.current_group_pubkey: str | None = None

        self._setup_ui()
        self._setup_menu()
        self._setup_status_bar()

        self.controller.event_for_ui.connect(self._on_new_event)
        self.controller.state_changed.connect(self._on_state_changed)
        self.controller.sync_finished.connect(self._on_sync_finished)
        self.controller.publish_failed.connect(self._on_publish_failed)
        self.controller.relay_status.connect(self._on_relay_status)
        self.controller.log_message.connect(self._on_log_message)
        self.controller.group_created.connect(self._on_group_created)
        self.controller.group_joined.connect(self._on_group_joined)
        self.controller.group_left.connect(self._on_group_left)
        self.controller.relays_changed.connect(self._on_relays_changed)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = RetroTitleBar(
            "  FERN Chat  ",
            "#004400",
            "#008833",
        )
        layout.addWidget(header)

        main_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(main_splitter, 1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(4)

        group_header = QLabel("Groups")
        group_header.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #000080; "
            "background-color: #B8B8B8; border: 1px solid #808080; padding: 3px;"
        )
        left_layout.addWidget(group_header)

        self.group_list = QListWidget()
        self.group_list.itemClicked.connect(self._on_group_selected)
        self.group_list.itemDoubleClicked.connect(self._on_group_double_clicked)
        self.group_list.blockSignals(True)
        self._populate_group_list()
        self.group_list.blockSignals(False)
        left_layout.addWidget(self.group_list, 1)

        group_btns = QHBoxLayout()
        group_btns.setSpacing(4)

        create_btn = QPushButton("Create")
        create_btn.setObjectName("SendButton")
        create_btn.clicked.connect(self._create_group)
        group_btns.addWidget(create_btn)

        join_btn = QPushButton("Join")
        join_btn.clicked.connect(self._join_group)
        group_btns.addWidget(join_btn)

        left_layout.addLayout(group_btns)

        main_splitter.addWidget(left_panel)

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        welcome = QWidget()
        welcome_layout = QVBoxLayout(welcome)
        welcome_layout.setAlignment(Qt.AlignCenter)

        welcome_title = QLabel("FERN Chat")
        welcome_title.setAlignment(Qt.AlignCenter)
        welcome_title.setStyleSheet(
            "font-size: 28px; font-weight: bold; color: #006600; "
            "font-family: 'MS Sans Serif', sans-serif;"
        )
        welcome_layout.addWidget(welcome_title)

        welcome_sub = QLabel("Fault-tolerant Event Relay Network")
        welcome_sub.setAlignment(Qt.AlignCenter)
        welcome_sub.setStyleSheet(
            "font-size: 12px; color: #666; font-style: italic; padding: 4px;"
        )
        welcome_layout.addWidget(welcome_sub)

        welcome_info = QLabel(
            "Select a group to start chatting.\n\n"
            "FERN is a decentralized group chat protocol.\n"
            "Groups are identified by public key, relayed through\n"
            "websockets servers of your choosing.\n\n"
            "Create a new group or join an existing one\n"
            "using a group address (pubkey@relay1,relay2)."
        )
        welcome_info.setAlignment(Qt.AlignCenter)
        welcome_info.setStyleSheet("font-size: 11px; color: #444; padding: 12px;")
        welcome_layout.addWidget(welcome_info)

        self.tab_widget.addTab(welcome, "Welcome")
        center_layout.addWidget(self.tab_widget)

        main_splitter.addWidget(center_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(2, 2, 2, 2)
        right_layout.setSpacing(4)

        member_header = QLabel("Members")
        member_header.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #000080; "
            "background-color: #B8B8B8; border: 1px solid #808080; padding: 3px;"
        )
        right_layout.addWidget(member_header)

        self.member_list = QListWidget()
        self._populate_member_list()
        right_layout.addWidget(self.member_list, 1)

        event_header = QLabel("Event Log")
        event_header.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #000080; "
            "background-color: #B8B8B8; border: 1px solid #808080; padding: 3px;"
        )
        right_layout.addWidget(event_header)

        self.event_log = QTextEdit()
        self.event_log.setObjectName("EventLog")
        self.event_log.setReadOnly(True)
        self.event_log.setFixedHeight(120)
        right_layout.addWidget(self.event_log)

        main_splitter.addWidget(right_panel)

        main_splitter.setSizes([200, 570, 140])

        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setStretchFactor(2, 0)

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        new_identity_action = QAction("New Identity...", self)
        new_identity_action.triggered.connect(self._new_identity)
        file_menu.addAction(new_identity_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(QApplication.quit)
        file_menu.addAction(quit_action)

        settings_menu = menubar.addMenu("Settings")
        self._retro_toggle_action = QAction("Retro Theme", self, checkable=True)
        self._retro_toggle_action.setChecked(True)
        self._retro_toggle_action.triggered.connect(self._toggle_retro_theme)
        settings_menu.addAction(self._retro_toggle_action)

        group_menu = menubar.addMenu("Group")
        create_action = QAction("Create Group...", self)
        create_action.setShortcut("Ctrl+N")
        create_action.triggered.connect(self._create_group)
        group_menu.addAction(create_action)

        join_action = QAction("Join Group...", self)
        join_action.setShortcut("Ctrl+J")
        join_action.triggered.connect(self._join_group)
        group_menu.addAction(join_action)

        group_menu.addSeparator()
        leave_action = QAction("Leave Group", self)
        leave_action.triggered.connect(self._leave_group)
        group_menu.addAction(leave_action)

        help_menu = menubar.addMenu("Help")
        about_action = QAction("About FERN", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_status_bar(self):
        user_pubkey, _ = self.controller.get_identity()
        self.status_label = QLabel(f"Identity: {short_key(user_pubkey)}")
        self.statusBar().addWidget(self.status_label)

        self.group_count_label = QLabel("Groups: 0")
        self.statusBar().addPermanentWidget(self.group_count_label)

        self.event_count_label = QLabel("Events: 0")
        self.statusBar().addPermanentWidget(self.event_count_label)

    def _toggle_retro_theme(self, checked: bool):
        app = QApplication.instance()
        if app:
            app.setStyleSheet(RETRO_STYLESHEET if checked else "")

    def _populate_group_list(self):
        self.group_list.clear()
        groups = self.controller.list_groups()
        for group_info in groups:
            item = GroupListItem(group_info, group_info.get("joined", False))
            self.group_list.addItem(item)
        if hasattr(self, "group_count_label") and self.group_count_label is not None:
            self.group_count_label.setText(f"Groups: {len(groups)}")

    def _populate_member_list(self):
        if not hasattr(self, "member_list") or self.member_list is None:
            return
        self.member_list.clear()
        if not self.current_group_pubkey:
            return

        state = self.controller.get_group_state(self.current_group_pubkey)
        user_pubkey, _ = self.controller.get_identity()

        if not state:
            return

        founder = state.genesis["content"].get("founder", "") if state.genesis else ""
        role_order = {"founder": 0, "mod": 1, "member": 2}

        sorted_members = sorted(
            state.joined,
            key=lambda p: (role_order.get(get_role(p, state), 3), p),
        )

        for pubkey in sorted_members:
            widget = MemberItemWidget(pubkey, state, user_pubkey)
            widget.member_clicked.connect(self._show_user_profile)
            item = QListWidgetItem(self.member_list)
            item.setSizeHint(widget.sizeHint())
            self.member_list.addItem(item)
            self.member_list.setItemWidget(item, widget)

    def _show_user_profile(self, pubkey: str):
        if not self.current_group_pubkey:
            return
        state = self.controller.get_group_state(self.current_group_pubkey)
        user_pubkey, _ = self.controller.get_identity()
        events = self.controller.get_group_events(self.current_group_pubkey)
        dialog = UserProfileDialog(pubkey, state, user_pubkey, events, self)
        dialog.exec_()

    def _on_group_selected(self, item):
        if isinstance(item, GroupListItem):
            self.current_group_pubkey = item.group_info.get("pubkey")
            self._populate_member_list()
            self._update_event_count()
            self._open_group(item.group_info.get("pubkey"), select_only=True)

    def _on_group_double_clicked(self, item):
        if isinstance(item, GroupListItem):
            self._open_group(item.group_info.get("pubkey"))

    def _open_group(self, group_pubkey: str, select_only: bool = False):
        if group_pubkey not in self.chat_views:
            chat_view = GroupChatView(group_pubkey, self.controller)
            chat_view.pubkey_clicked.connect(self._show_user_profile_from_chat)
            self.chat_views[group_pubkey] = chat_view

            state = self.controller.get_group_state(group_pubkey)
            name = (
                state.metadata.get("name", f"Group_{group_pubkey[:8]}")
                if state
                else "Group"
            )
            tab_name = name
            if len(tab_name) > 15:
                tab_name = tab_name[:12] + "..."
            self.tab_widget.addTab(chat_view, tab_name)

            self.controller.sync_group(group_pubkey)
            self.controller.subscribe_group(group_pubkey)

        self.tab_widget.setCurrentWidget(self.chat_views[group_pubkey])

        if not select_only:
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                if (
                    isinstance(item, GroupListItem)
                    and item.group_info.get("pubkey") == group_pubkey
                ):
                    self.group_list.blockSignals(True)
                    self.group_list.setCurrentItem(item)
                    self.group_list.blockSignals(False)
                    break

        self.current_group_pubkey = group_pubkey
        self._populate_member_list()

    def _show_user_profile_from_chat(self, pubkey: str):
        if not self.current_group_pubkey:
            return
        state = self.controller.get_group_state(self.current_group_pubkey)
        user_pubkey, _ = self.controller.get_identity()
        events = self.controller.get_group_events(self.current_group_pubkey)
        dialog = UserProfileDialog(pubkey, state, user_pubkey, events, self)
        dialog.exec_()

    def _close_tab(self, index):
        if index == 0:
            return
        widget = self.tab_widget.widget(index)
        for pubkey, view in list(self.chat_views.items()):
            if view == widget:
                self.controller.unsubscribe_group(pubkey)
                del self.chat_views[pubkey]
                break
        self.tab_widget.removeTab(index)

    def _close_tab_by_pubkey(self, group_pubkey: str):
        """Close the tab for a group and clean up subscriptions."""
        if group_pubkey not in self.chat_views:
            return
        view = self.chat_views[group_pubkey]
        for i in range(1, self.tab_widget.count()):
            if self.tab_widget.widget(i) == view:
                self.controller.unsubscribe_group(group_pubkey)
                self.tab_widget.removeTab(i)
                del self.chat_views[group_pubkey]
                break

    def _on_tab_changed(self, index):
        if index == 0:
            self.current_group_pubkey = None
            self._populate_member_list()
            self.group_list.blockSignals(True)
            self.group_list.clearSelection()
            self.group_list.blockSignals(False)
            return

        widget = self.tab_widget.widget(index)
        for group_pubkey, view in self.chat_views.items():
            if view == widget:
                self.current_group_pubkey = group_pubkey
                self._populate_member_list()
                self.group_list.blockSignals(True)
                for i in range(self.group_list.count()):
                    item = self.group_list.item(i)
                    if (
                        isinstance(item, GroupListItem)
                        and item.group_info.get("pubkey") == group_pubkey
                    ):
                        self.group_list.setCurrentItem(item)
                        break
                self.group_list.blockSignals(False)
                break

    def _create_group(self):
        dialog = CreateGroupDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.controller.create_group(
                dialog.group_name,
                dialog.group_desc,
                dialog.is_public,
                dialog.relays,
            )

    def _join_group(self):
        dialog = JoinGroupDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.controller.join_group(dialog.address)

    def _leave_group(self):
        if not self.current_group_pubkey:
            QMessageBox.information(self, "Leave Group", "No group selected.")
            return

        state = self.controller.get_group_state(self.current_group_pubkey)
        if state and state.genesis:
            founder = state.genesis["content"].get("founder", "")
            user_pubkey, _ = self.controller.get_identity()
            if user_pubkey == founder:
                QMessageBox.warning(
                    self,
                    "Cannot Leave",
                    "You are the founder. You cannot leave your own group.",
                )
                return

        reply = QMessageBox.question(
            self,
            "Leave Group",
            f"Are you sure you want to leave this group?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.controller.leave_group(self.current_group_pubkey)

    def _on_new_event(self, group_pubkey: str, event: dict):
        if group_pubkey in self.chat_views:
            view = self.chat_views[group_pubkey]
            eid = event.get("id")
            if eid not in view._displayed_ids:
                view._append_event(event)
                view._displayed_ids.add(eid)
                view._last_event_id = eid
        self._update_event_count()

    def _on_state_changed(self, group_pubkey: str):
        self._populate_group_list()
        if self.current_group_pubkey == group_pubkey:
            self._populate_member_list()
            if group_pubkey in self.chat_views:
                view = self.chat_views[group_pubkey]
                if hasattr(view, "join_btn") and view.join_btn:
                    is_joined = self.controller.is_joined(group_pubkey)
                    view.join_btn.setVisible(not is_joined)
        self._update_event_count()

    def _on_sync_finished(self, group_pubkey: str):
        self.statusBar().showMessage(f"Synced: {short_key(group_pubkey)}", 3000)
        if group_pubkey in self.chat_views:
            view = self.chat_views[group_pubkey]
            view.refresh_header()
            view._append_new_events()

    def _on_publish_failed(self, event_id: str, error: str):
        self.statusBar().showMessage(f"Publish failed: {error}", 5000)

    def _on_relay_status(self, group_pubkey: str, relay_url: str, status: str):
        if group_pubkey in self.chat_views:
            view = self.chat_views[group_pubkey]
            view.relay_bar.set_relay_status(relay_url, status)
        short_relay = relay_url.replace("wss://", "").replace("ws://", "")
        if status == "connected":
            self._on_log_message("info", f"Relay {short_relay} connected")
        elif status == "disconnected":
            self._on_log_message("warning", f"Relay {short_relay} disconnected")
        elif status == "reconnecting":
            self._on_log_message("info", f"Relay {short_relay} reconnecting...")

    def _on_relays_changed(self, group_pubkey: str, relay_urls: list[str]):
        if group_pubkey not in self.chat_views:
            return
        self.controller.unsubscribe_group(group_pubkey)
        self.controller.subscribe_group(group_pubkey)
        view = self.chat_views[group_pubkey]
        view.relay_bar.set_relays(relay_urls)
        view.refresh_header()

    def _on_log_message(self, level: str, message: str):
        """Append a timestamped, level-tagged message to the event log."""
        from datetime import datetime
        import sys

        ts = datetime.now().strftime("%H:%M:%S")
        level_tags = {"error": "[ERR]", "warning": "[WRN]", "info": "[INF]"}
        tag = level_tags.get(level, f"[{level.upper()[:3]}]")
        color_map = {
            "error": "#CC0000",
            "warning": "#CC6600",
            "info": "#006600",
        }
        color = color_map.get(level, "#666666")
        log_line = f"[{ts}] {tag} {message}"

        event_log = getattr(self, "event_log", None)
        if event_log is not None:
            try:
                cursor = event_log.textCursor()
                cursor.movePosition(cursor.End)
                fmt = QTextCharFormat()
                fmt.setFont(QFont("Courier New", 9))
                fmt.setForeground(QColor("#666666"))
                cursor.setCharFormat(fmt)
                cursor.insertText(f"[{ts}] ")
                fmt.setForeground(QColor(color))
                cursor.setCharFormat(fmt)
                cursor.insertText(f"{tag} ")
                fmt.setForeground(QColor("#333333"))
                cursor.setCharFormat(fmt)
                cursor.insertText(f"{message}\n")
                event_log.setTextCursor(cursor)
                event_log.ensureCursorVisible()
            except Exception as e:
                print(log_line, file=sys.stderr)
        else:
            print(log_line, file=sys.stderr)

    def _on_group_created(self, group_pubkey: str):
        self._populate_group_list()
        self._open_group(group_pubkey)
        self.statusBar().showMessage(f"Created group", 3000)

    def _on_group_joined(self, group_pubkey: str):
        self._populate_group_list()
        self._open_group(group_pubkey)
        self.statusBar().showMessage(f"Joined group", 3000)

    def _on_group_left(self, group_pubkey: str):
        self._populate_group_list()
        if self.current_group_pubkey == group_pubkey:
            self.current_group_pubkey = None
            self._populate_member_list()
        self._close_tab_by_pubkey(group_pubkey)

    def _new_identity(self):
        dialog = IdentityDialog(self.controller, self)
        if dialog.exec_() == QDialog.Accepted:
            user_pubkey, _ = self.controller.get_identity()
            self.status_label.setText(f"Identity: {short_key(user_pubkey)}")
            self.statusBar().showMessage("New identity created", 3000)

    def _update_event_count(self):
        total = 0
        for group_info in self.controller.list_groups():
            total += group_info.get("event_count", 0)
        self.event_count_label.setText(f"Events: {total}")

    def _show_about(self):
        QMessageBox.about(
            self,
            "About FERN",
            "FERN Chat\n\n"
            "Fault-tolerant Event Relay Network, built for decentralized public group chats.\n"
            "Built with PyQt5",
        )
