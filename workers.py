from threading import Event
from collections.abc import Callable
import logging
from PySide6.QtCore import QThread, Signal
from duplicate_finder import CancelledError

class Worker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int)

    def __init__(self, task: Callable, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel_event = Event()

    def run(self):
        try:
            result = self.task(self.cancel_event, self.progress.emit)
            self.result.emit(result)
        except CancelledError as error:
            self.error.emit(str(error))
        except Exception as error:
            logging.exception('Background task failed')
            self.error.emit(str(error))

    def cancel(self):
        self.cancel_event.set()
