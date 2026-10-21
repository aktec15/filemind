import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
from database import Database
from main_window import MainWindow


def test_window_scan_search_and_navigation(tmp_path):
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / 'files'
    folder.mkdir()
    (folder / 'sample.txt').write_text('hello')
    (folder / 'photo.png').write_bytes(b'png')
    window = MainWindow(Database(tmp_path / 'db.sqlite'))
    window.open_folder(folder)
    loop = QEventLoop()
    window.worker.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert not window.busy()
    assert window.model.rowCount() == 2
    window.search.setText('sample')
    assert window.proxy.rowCount() == 1
    window.clear_filters()
    assert window.proxy.rowCount() == 2
    window.navigation.setCurrentRow(3)
    assert window.proxy.rowCount() == 1
    window.add_favorite()
    assert window.favorite_list.count() == 1
    for index in (7, 8, 9, 0):
        window.navigation.setCurrentRow(index)
    window.show()
    app.processEvents()
    screenshot = os.environ.get('FILEMIND_SCREENSHOT')
    if screenshot:
        window.grab().save(screenshot)
    window.close()


def test_background_duplicates_and_organize(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    from main_window import PreviewDialog
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / 'files'
    folder.mkdir()
    (folder / 'a.txt').write_bytes(b'identical')
    (folder / 'b.txt').write_bytes(b'identical')
    window = MainWindow(Database(tmp_path / 'db.sqlite'))
    errors = []
    monkeypatch.setattr(window, 'inform', lambda *args: None)
    monkeypatch.setattr(window, 'report_error', errors.append)

    def settle(condition):
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if condition() else None)
        timer.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        timer.stop()
        app.processEvents()
        assert condition()

    window.open_folder(folder)
    settle(lambda: not window.busy())
    window.find_duplicates()
    settle(lambda: not window.busy())
    assert len(window.groups) == 1
    assert window.duplicate_tree.topLevelItem(0).childCount() == 2
    monkeypatch.setattr(PreviewDialog, 'exec', lambda self: QDialog.Accepted)
    window.organize()
    settle(lambda: len(window.db.history()) == 2 and not window.busy())
    settle(lambda: (folder / 'Documents' / 'a.txt').exists())
    # Let the deferred post-operation rescan complete before closing.
    loop = QEventLoop()
    QTimer.singleShot(250, loop.quit)
    loop.exec()
    settle(lambda: not window.busy())
    assert not errors
    assert window.model.rowCount() == 2
    window.close()
