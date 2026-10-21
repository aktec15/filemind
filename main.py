import logging
import sys
from logging.handlers import RotatingFileHandler
from PySide6.QtWidgets import QApplication, QMessageBox
from database import Database
from main_window import MainWindow
from settings import data_directory


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('FileMind')
    app.setOrganizationName('FileMind')
    app.setStyle('Fusion')
    try:
        directory = data_directory()
        logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
            directory / 'filemind.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')])
        window = MainWindow(Database(directory / 'filemind.db'))
        window.show()
    except Exception as error:
        logging.exception('Could not start FileMind')
        QMessageBox.critical(None, 'FileMind could not start',
            f'Could not open application settings or initialize the window.\n{error}')
        return 1
    return app.exec()

if __name__ == '__main__':
    raise SystemExit(main())
