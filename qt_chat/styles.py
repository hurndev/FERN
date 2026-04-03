"""Retro stylesheet and relay color palette for the Qt chat app."""

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
