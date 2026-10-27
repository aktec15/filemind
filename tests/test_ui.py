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
    (folder / 'notes').mkdir()
    (folder / 'notes' / 'a.txt').write_bytes(b'identical')
    (folder / 'notes' / 'b.txt').write_bytes(b'identical')
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
    settle(lambda: len(window.db.history()) == 1 and not window.busy())
    settle(lambda: (folder / 'Documents' / 'notes' / 'a.txt').exists())
    # Let the deferred post-operation rescan complete before closing.
    loop = QEventLoop()
    QTimer.singleShot(250, loop.quit)
    loop.exec()
    settle(lambda: not window.busy())
    assert not errors
    assert window.model.rowCount() == 2
    window.close()


def test_ai_advisor_review_move_and_restore(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    import main_window
    from ai_settings import Connection
    from ai_advisor import Advice
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'files'
    (root / 'Альфа Портал/src').mkdir(parents=True)
    (root / 'Альфа Портал/package.json').write_text('{}')
    (root / 'Альфа Портал/src/main.js').write_text('source')
    (root / 'Без названия').mkdir()
    (root / 'Без названия/notes.txt').write_text('notes')
    db = Database(tmp_path / 'db.sqlite')
    db.set('ai_settings', {'folder_id': 'folder', 'model': 'model', 'workspaces': [
        {'name': 'Альфа', 'description': 'Компания Альфа, проект Альфа Портал'},
        {'name': 'Бета', 'description': 'Компания Бета'}]})
    window = MainWindow(db)
    errors = []
    monkeypatch.setattr(window, 'report_error', errors.append)
    monkeypatch.setattr(window, 'inform', lambda *args: None)
    monkeypatch.setattr(main_window, 'connection_from', lambda config: Connection('folder', 'model', 'fake'))
    outbound = []
    def suggest(self, payload, cancel):
        outbound.append(payload)
        return [Advice(item['id'], 0 if 'Альфа' in item['folder'] else -1, 'Проверено по названию')
                for item in payload['folders']]
    monkeypatch.setattr(main_window.YandexAdvisor, 'suggest', suggest)
    monkeypatch.setattr(main_window.MetadataDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(main_window.AdviceDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(main_window.PreviewDialog, 'exec', lambda self: QDialog.Accepted)
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
        assert condition(), errors
    window.open_folder(root)
    settle(lambda: not window.busy())
    window.ai_advise()
    target = root / 'Workspaces/Альфа/Projects/Альфа Портал/src/main.js'
    settle(lambda: target.exists() and not window.busy())
    assert len(outbound) == 1
    assert target.read_text() == 'source'
    assert (root / 'Без названия/notes.txt').exists()
    assert len(db.history()) == 1
    assert not errors
    loop = QEventLoop()
    QTimer.singleShot(250, loop.quit)
    loop.exec()
    settle(lambda: not window.busy())
    window.organizer.undo(db.history()[0]['id'])
    assert (root / 'Альфа Портал/src/main.js').read_text() == 'source'
    window.close()


def test_ai_settings_save_keeps_api_key_out_of_database(tmp_path, monkeypatch):
    from ai_dialogs import AISettingsDialog
    import ai_dialogs
    app = QApplication.instance() or QApplication([])
    db = Database(tmp_path / 'db.sqlite')
    saved_keys = []
    monkeypatch.setattr(ai_dialogs, 'save_api_key', saved_keys.append)
    dialog = AISettingsDialog({'folder_id': 'folder', 'model': 'model', 'workspaces': []}, db)
    dialog.workspaces.setPlainText('Альфа | Проекты компании Альфа\nБета | Проекты компании Бета')
    dialog.api_key.setText('FAKE_SECRET_API_KEY')
    dialog.save()
    assert saved_keys == ['FAKE_SECRET_API_KEY']
    assert dialog.api_key.text() == ''
    assert len(db.get('ai_settings')['workspaces']) == 2
    assert b'FAKE_SECRET_API_KEY' not in db.path.read_bytes()
    dialog.close()
