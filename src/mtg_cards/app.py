from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from mtg_cards.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MTG Cards")

    window = MainWindow()
    window.show()

    return app.exec()
