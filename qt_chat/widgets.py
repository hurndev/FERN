"""Shared widget classes and helper functions for the Qt chat app."""

from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QFont,
    QColor,
    QPainter,
    QPen,
    QBrush,
    QLinearGradient,
)
from PyQt5.QtWidgets import QFrame, QLabel, QListWidgetItem, QWidget, QHBoxLayout

from .styles import RELAY_COLORS


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
