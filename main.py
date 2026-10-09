"""魔云腾中控入口。"""

from __future__ import annotations

import sys
import threading

from PySide6.QtWidgets import QApplication

from app.config import prune_debug_logs, record_exception
from app.main_window import MainWindow


def _install_hooks() -> None:
    def _sys(exc_type, exc, tb):
        record_exception("主线程", exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)

    def _thread(args):
        record_exception("线程", args.exc_type, args.exc_value, args.exc_traceback)

    def _unraisable(unraisable):
        record_exception(
            "unraisable",
            unraisable.exc_type,
            unraisable.exc_value,
            unraisable.exc_traceback,
        )

    sys.excepthook = _sys
    threading.excepthook = _thread
    sys.unraisablehook = _unraisable


def main() -> None:
    _install_hooks()
    prune_debug_logs()
    app = QApplication(sys.argv)
    app.setApplicationName("MoyuntengControl")
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
