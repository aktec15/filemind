"""Folder organization must preserve projects, unrelated files and history."""
import errno
from threading import Event

import pytest

from database import Database
from duplicate_finder import CancelledError
from file_organizer import FileOrganizer


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


@pytest.mark.parametrize('marker', ['pyproject.toml', 'package.json', 'Cargo.toml',
                                     'go.mod', '.git/config', 'requirements.txt'])
def test_project_moves_as_one_folder_and_restores(workspace, marker):
    root, db, organizer = workspace
    write(root, f'app/{marker}')
    write(root, 'app/src/main.py')
    write(root, 'app/.env', b'secret')
    write(root, 'app/assets/photo.png', b'image')
    (root / 'app/empty').mkdir()
    loose = write(root, 'loose.py')
    plans = organizer.preview(root)
    assert len(plans) == 1
    assert plans[0].source == root / 'app'
    assert plans[0].destination == root / 'Projects/app'
    assert organizer.execute(plans, root) == (1, [])
    assert not (root / 'app').exists()
    assert (root / 'Projects/app/.env').read_bytes() == b'secret'
    assert (root / 'Projects/app/empty').is_dir()
    assert loose.exists()
    assert organizer.preview(root) == []
    organizer.undo(db.history()[0]['id'])
    assert (root / f'app/{marker}').exists()
    assert (root / 'app/src/main.py').exists()
    assert not (root / 'Projects/app').exists()


def test_folder_categories_preserve_internal_structure(workspace):
    root, _, organizer = workspace
    write(root, 'photos/2026/a.jpg')
    write(root, 'photos/2025/b.png')
    write(root, 'mixed/a.txt')
    write(root, 'mixed/b.jpg')
    (root / 'empty').mkdir()
    write(root, '.hidden/a.txt')
    plans = organizer.preview(root)
    assert {plan.destination.relative_to(root).as_posix() for plan in plans} == {
        'Images/photos', 'Folders/mixed', 'Folders/empty'}
    assert organizer.execute(plans, root) == (3, [])
    assert (root / 'Images/photos/2026/a.jpg').exists()
    assert (root / 'Images/photos/2025/b.png').exists()
    assert (root / 'Folders/mixed/b.jpg').exists()
    assert (root / '.hidden/a.txt').exists()


def test_nested_project_keeps_enclosing_folder(workspace):
    root, _, organizer = workspace
    write(root, 'bundle/app/package.json')
    write(root, 'bundle/readme.txt')
    plan, = organizer.preview(root)
    assert plan.source == root / 'bundle'
    assert plan.destination == root / 'Projects/bundle'


def test_selecting_project_root_does_not_move_its_subfolders(workspace):
    root, _, organizer = workspace
    write(root, 'pyproject.toml')
    write(root, 'src/main.py')
    with pytest.raises(ValueError, match='parent folder'):
        organizer.preview(root)
    assert (root / 'src/main.py').exists()


@pytest.mark.parametrize('occupied', [False, True])
def test_late_directory_conflict_never_replaced(workspace, occupied):
    root, db, organizer = workspace
    write(root, 'notes/a.txt', b'original')
    plan, = organizer.preview(root)
    plan.destination.mkdir(parents=True)
    if occupied:
        write(plan.destination, 'keep.txt', b'keep')
    with pytest.raises(FileExistsError):
        organizer.move(plan, root)
    assert (root / 'notes/a.txt').read_bytes() == b'original'
    assert plan.destination.is_dir()
    assert not (plan.destination / 'a.txt').exists()
    if occupied:
        assert (plan.destination / 'keep.txt').read_bytes() == b'keep'
    assert db.history()[0]['status'] == 'failed'


def test_folder_conflict_preserves_dotted_name(workspace):
    root, _, organizer = workspace
    write(root, 'app.v1/package.json')
    (root / 'Projects/app.v1').mkdir(parents=True)
    plan, = organizer.preview(root)
    assert plan.destination.name == 'app.v1 (1)'
    assert plan.conflict


def test_project_symlinks_preserved_without_touching_target(workspace, tmp_path):
    root, db, organizer = workspace
    write(root, 'app/pyproject.toml')
    write(root, 'app/src/main.py')
    outside = write(tmp_path, 'outside/data.txt', b'outside')
    (root / 'app/data').symlink_to(outside.parent, target_is_directory=True)
    (root / 'app/main.py').symlink_to('src/main.py')
    plan, = organizer.preview(root)
    organizer.move(plan, root)
    assert (plan.destination / 'data').is_symlink()
    assert (plan.destination / 'main.py').readlink().as_posix() == 'src/main.py'
    organizer.undo(db.history()[0]['id'])
    assert (root / 'app/main.py').is_symlink()
    assert outside.read_bytes() == b'outside'


@pytest.mark.parametrize('change', ['edit', 'add', 'empty_directory', 'link'])
def test_restore_refuses_changed_folder(workspace, change):
    root, db, organizer = workspace
    write(root, 'notes/a.txt')
    plan, = organizer.preview(root)
    organizer.move(plan, root)
    if change == 'edit':
        (plan.destination / 'a.txt').write_bytes(b'changed')
    elif change == 'add':
        write(plan.destination, 'new.txt')
    elif change == 'empty_directory':
        (plan.destination / 'empty').mkdir()
    else:
        (plan.destination / 'alias').symlink_to('a.txt')
    with pytest.raises(ValueError, match='changed'):
        organizer.undo(db.history()[0]['id'])
    assert plan.destination.is_dir()
    assert not plan.source.exists()


def test_folder_cancellation_after_hash_keeps_original(workspace, monkeypatch):
    root, db, organizer = workspace
    write(root, 'notes/a.txt')
    plan, = organizer.preview(root)
    cancel = Event()
    real_begin = db.begin_operation
    def begin(*args):
        operation_id = real_begin(*args)
        cancel.set()
        return operation_id
    monkeypatch.setattr(db, 'begin_operation', begin)
    with pytest.raises(CancelledError):
        organizer.move(plan, root, cancel=cancel)
    assert (root / 'notes/a.txt').exists()
    assert not plan.destination.exists()
    assert db.history()[0]['status'] == 'failed'


def test_cross_filesystem_failure_keeps_whole_source(workspace, monkeypatch):
    root, db, organizer = workspace
    write(root, 'notes/a.txt')
    plan, = organizer.preview(root)
    def fail(*args):
        raise OSError(errno.EXDEV, 'Cross-device link')
    monkeypatch.setattr('file_organizer.rename_folder', fail)
    assert organizer.execute([plan], root)[0] == 0
    assert (root / 'notes/a.txt').exists()
    assert not plan.destination.exists()
    assert db.history()[0]['status'] == 'failed'


def test_existing_folder_history_is_compatible_with_file_history(workspace):
    root, db, organizer = workspace
    from file_organizer import MovePlan
    file = write(root, 'loose.txt')
    file_id = organizer.move(MovePlan(file, root / '.filemind-trash/loose.txt'), root, 'quarantine')
    write(root, 'notes/a.txt')
    plan, = organizer.preview(root)
    organizer.move(plan, root)
    organizer.undo(file_id)
    assert file.read_bytes() == b'hello'
    assert plan.destination.is_dir()
