from pathlib import Path
from threading import Event
import pytest
from database import Database
from models import FileRecord, category_for
from file_scanner import FileScanner
from file_search import SearchFilters, search_files
from duplicate_finder import DuplicateFinder, CancelledError
from file_organizer import FileOrganizer, MovePlan, duplicate_removal_plan

@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / 'files'
    root.mkdir()
    db = Database(tmp_path / 'test.db')
    return root, db, FileOrganizer(db)

def write(root, name, content=b'hello'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path

def records(root):
    return FileScanner().scan(root)[0]

def test_recursive_scan_excludes_links_and_quarantine(workspace):
    root, _, _ = workspace
    write(root, 'nested/REPORT.PDF')
    write(root, '.filemind-trash/hidden.txt')
    (root / 'cycle').symlink_to(root, target_is_directory=True)
    (root / 'alias.pdf').symlink_to(root / 'nested/REPORT.PDF')
    result, errors = FileScanner().scan(root)
    assert len(result) == 1
    assert result[0].category == 'Documents'
    assert not errors

def test_cancelled_scan(workspace):
    root, _, _ = workspace
    write(root, 'a.txt')
    cancel = Event()
    cancel.set()
    assert FileScanner().scan(root, cancel)[0] == []

def test_filters(workspace):
    root, _, _ = workspace
    first = FileRecord.from_path(write(root, 'REPORT.PDF', b'123456'))
    second = FileRecord.from_path(write(root, 'a.png', b'123'))
    filters = SearchFilters(query='port', extension='.PDF', category='Documents', min_size=5,
        max_size=7, modified_after=first.modified - 1, modified_before=first.modified + 1)
    assert search_files([first, second], filters) == [first]
    filters.max_size = 5
    assert search_files([first], filters) == []

def test_duplicate_contents_not_names(workspace):
    root, _, _ = workspace
    a = write(root, 'a.txt', b'same')
    b = write(root, 'nested/b.bin', b'same')
    write(root, 'c.txt', b'diff')
    write(root, 'empty1', b'')
    write(root, 'empty2', b'')
    groups, errors = DuplicateFinder().find(records(root))
    assert {a, b} in [{r.path for r in group} for group in groups]
    assert len(groups) == 2
    assert not errors

def test_duplicate_cancel(workspace):
    root, _, _ = workspace
    write(root, 'a', b'same')
    write(root, 'b', b'same')
    cancel = Event()
    cancel.set()
    with pytest.raises(CancelledError):
        DuplicateFinder().find(records(root), cancel)

def test_organize_preview_conflicts_undo(workspace):
    root, db, organizer = workspace
    source = write(root, 'notes/a.txt', b'new')
    existing = write(root, 'Documents/notes/old.txt', b'old')
    plans = organizer.preview(root)
    assert len(plans) == 1
    assert plans[0].conflict
    assert plans[0].is_directory
    assert source.exists()  # Preview must not modify anything.
    count, errors = organizer.execute(plans, root)
    assert (count, errors) == (1, [])
    assert existing.read_bytes() == b'old'
    assert not source.exists()
    organizer.undo(db.history()[0]['id'])
    assert source.read_bytes() == b'new'
    assert db.history()[1]['status'] == 'restored'

def test_late_conflict_never_overwritten(workspace):
    root, db, organizer = workspace
    source = write(root, 'a.txt')
    plan = MovePlan(source, root / 'Documents' / source.name)
    write(root, 'Documents/a.txt', b'do not overwrite')
    with pytest.raises(FileExistsError):
        organizer.move(plan, root)
    assert source.read_bytes() == b'hello'
    assert plan.destination.read_bytes() == b'do not overwrite'
    assert db.history()[0]['status'] == 'failed'

def test_undo_protects_occupied_source(workspace):
    root, db, organizer = workspace
    source = write(root, 'a.txt')
    organizer.move(MovePlan(source, root / 'Documents' / source.name), root)
    source.write_bytes(b'new data')
    with pytest.raises(FileExistsError):
        organizer.undo(db.history()[0]['id'])
    assert source.read_bytes() == b'new data'

def test_undo_protects_modified_destination(workspace):
    root, db, organizer = workspace
    source = write(root, 'a.txt')
    plan = MovePlan(source, root / 'Documents' / source.name)
    organizer.move(plan, root)
    plan.destination.write_bytes(b'edited')
    with pytest.raises(ValueError):
        organizer.undo(db.history()[0]['id'])
    assert plan.destination.read_bytes() == b'edited'

def test_cancelled_move_keeps_original(workspace):
    root, _, organizer = workspace
    source = write(root, 'a.txt')
    plan = MovePlan(source, root / 'Documents' / source.name)
    cancel = Event()
    cancel.set()
    assert organizer.execute([plan], root, cancel) == (0, [])
    assert source.exists()
    assert not plan.destination.exists()

def test_symlink_target_rejected(workspace, tmp_path):
    root, _, organizer = workspace
    source = write(root, 'a.txt')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'Documents').symlink_to(outside, target_is_directory=True)
    (root / 'notes').mkdir()
    write(root, 'notes/b.txt')
    with pytest.raises(ValueError):
        organizer.preview(root)
    assert source.exists()
    assert not list(outside.iterdir())

def test_duplicate_last_copy_and_recovery(workspace):
    root, db, organizer = workspace
    a = write(root, 'a.txt', b'same')
    b = write(root, 'b.txt', b'same')
    groups, _ = DuplicateFinder().find(records(root))
    with pytest.raises(ValueError):
        duplicate_removal_plan(groups, {a, b}, root)
    plans = duplicate_removal_plan(groups, {a}, root)
    organizer.execute(plans, root, kind='quarantine')
    assert b.exists() and not a.exists()
    assert len(records(root)) == 1
    organizer.undo(db.history()[0]['id'])
    assert a.read_bytes() == b'same'

def test_duplicate_changed_survivor(workspace):
    root, _, _ = workspace
    a = write(root, 'a.txt', b'same')
    b = write(root, 'b.txt', b'same')
    groups, _ = DuplicateFinder().find(records(root))
    b.write_bytes(b'changed')
    with pytest.raises(ValueError):
        duplicate_removal_plan(groups, {a}, root)
    assert a.exists()

def test_database_persistence(workspace):
    root, db, _ = workspace
    db.set('theme', 'Light')
    db.add_favorite(root)
    db.add_favorite(root)
    reopened = Database(db.path)
    assert reopened.get('theme') == 'Light'
    assert len(reopened.favorites()) == 1
    reopened.remove_favorite(str(root))
    assert reopened.favorites() == []

def test_access_error_is_reported(workspace, monkeypatch):
    root, _, _ = workspace
    path = write(root, 'locked.txt')
    original = FileRecord.from_path
    def read(record_path):
        if record_path == path:
            raise PermissionError('Access denied')
        return original(record_path)
    monkeypatch.setattr(FileRecord, 'from_path', read)
    result, errors = FileScanner().scan(root)
    assert result == []
    assert len(errors) == 1 and 'Access denied' in errors[0]

def test_copy_failure_keeps_source_and_cleans_partial(workspace, monkeypatch):
    root, db, organizer = workspace
    source = write(root, 'a.txt')
    plan = MovePlan(source, root / 'Documents' / source.name)
    monkeypatch.setattr('file_organizer.os.fsync', lambda _: (_ for _ in ()).throw(OSError('Disk full')))
    with pytest.raises(OSError, match='Disk full'):
        organizer.move(plan, root)
    assert source.read_bytes() == b'hello'
    assert not plan.destination.exists()
    assert db.history()[0]['status'] == 'failed'

def test_multiple_conflicts_in_one_preview(workspace):
    root, _, organizer = workspace
    write(root, 'one/notes/a.txt', b'one')
    write(root, 'two/notes/a.txt', b'two')
    plans = organizer.preview(root)
    assert len({p.destination for p in plans}) == 2
    assert organizer.execute(plans, root) == (2, [])
    assert {p.read_bytes() for p in (root / 'Documents').rglob('a.txt')} == {b'one', b'two'}

def test_parent_traversal_rejected(workspace, tmp_path):
    root, _, organizer = workspace
    source = write(root, 'a.txt')
    with pytest.raises(ValueError):
        organizer.move(MovePlan(source, root / '..' / 'escaped.txt'), root)
    assert source.exists()
    assert not (tmp_path / 'escaped.txt').exists()

def test_cancel_during_copy_keeps_source(workspace, monkeypatch):
    root, db, organizer = workspace
    source = write(root, 'a.txt', b'data' * 1000)
    plan = MovePlan(source, root / 'Documents' / source.name)
    cancel = Event()
    real_fsync = __import__('os').fsync
    def cancel_after_flush(fd):
        real_fsync(fd)
        cancel.set()
    monkeypatch.setattr('file_organizer.os.fsync', cancel_after_flush)
    with pytest.raises(CancelledError):
        organizer.move(plan, root, cancel=cancel)
    assert source.read_bytes() == b'data' * 1000
    assert not plan.destination.exists()
    assert db.history()[0]['status'] == 'failed'

def test_edit_during_copy_keeps_new_source(workspace, monkeypatch):
    root, _, organizer = workspace
    source = write(root, 'a.txt', b'original')
    plan = MovePlan(source, root / 'Documents' / source.name)
    real_fsync = __import__('os').fsync
    def edit_after_flush(fd):
        real_fsync(fd)
        source.write_bytes(b'edited by another program')
    monkeypatch.setattr('file_organizer.os.fsync', edit_after_flush)
    with pytest.raises(OSError, match='changed'):
        organizer.move(plan, root)
    assert source.read_bytes() == b'edited by another program'
    assert not plan.destination.exists()
