import os
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from collections.abc import Callable
from database import Database
from duplicate_finder import sha256, CancelledError
from models import FileRecord
from filesystem import is_link

@dataclass(frozen=True)
class MovePlan:
    source: Path
    destination: Path
    conflict: bool = False


def protected(path: Path) -> bool:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        return True
    if os.name == 'nt':
        for name in ('WINDIR', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA'):
            value = os.environ.get(name)
            if value and resolved.is_relative_to(Path(value).resolve()):
                return True
    return False


def validate_location(path: Path, root: Path) -> None:
    if not path.absolute().is_relative_to(root.absolute()):
        raise ValueError(f'Path escapes the selected folder: {path}')
    for component in (path, *path.parents):
        if (component.exists() and is_link(component)) or component.is_symlink():
            raise ValueError(f'Symbolic links are not supported: {component}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes the selected folder: {path}')
    if protected(path) or protected(root):
        raise ValueError('Choose a personal folder, not a drive root or system folder.')


def unique_destination(path: Path, reserved: set[Path]) -> Path:
    candidate = path
    index = 1
    while candidate.exists() or candidate.is_symlink() or candidate in reserved:
        candidate = path.with_name(f'{path.stem} ({index}){path.suffix}')
        index += 1
    return candidate

class FileOrganizer:
    def __init__(self, database: Database):
        self.database = database

    def preview(self, root: Path, records: list[FileRecord], cancel: Event | None = None) -> list[MovePlan]:
        root = root.absolute()
        reserved: set[Path] = set()
        plans = []
        for record in records:
            if cancel and cancel.is_set():
                raise CancelledError('Preview cancelled.')
            source = record.path.absolute()
            validate_location(source, root)
            target_folder = root / record.category
            if source.parent == target_folder:
                continue
            destination = target_folder / source.name
            validate_location(destination, root)
            safe = unique_destination(destination, reserved)
            reserved.add(safe)
            plans.append(MovePlan(source, safe, safe != destination))
        return plans

    def move(self, plan: MovePlan, root: Path, kind: str = 'organize',
             cancel: Event | None = None) -> int:
        source, destination = plan.source, plan.destination
        validate_location(source, root)
        validate_location(destination, root)
        if cancel and cancel.is_set():
            raise CancelledError('Operation cancelled.')
        fingerprint = sha256(source, cancel)
        operation_id = self.database.begin_operation(kind, source, destination, fingerprint)
        created = False
        removed = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            validate_location(destination, root)
            # Exclusive creation also protects against conflicts appearing after preview.
            with source.open('rb') as incoming, destination.open('xb') as outgoing:
                created = True
                while chunk := incoming.read(1024 * 1024):
                    if cancel and cancel.is_set():
                        raise CancelledError('Operation cancelled.')
                    outgoing.write(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if sha256(destination, cancel) != fingerprint or sha256(source, cancel) != fingerprint:
                raise OSError('File changed during the operation; source was kept.')
            shutil.copystat(source, destination, follow_symlinks=False)
            source.unlink()
            removed = True
            self.database.finish_operation(operation_id, 'completed')
            logging.info('%s: %s -> %s', kind, source, destination)
            return operation_id
        except BaseException as error:
            if created and not removed:
                try:
                    destination.unlink()
                except OSError:
                    pass
            self.database.finish_operation(operation_id, 'recovery_needed' if removed else 'failed', str(error))
            raise

    def execute(self, plans: list[MovePlan], root: Path, cancel: Event | None = None,
                progress: Callable[[int], None] = lambda _: None, kind: str = 'organize') -> tuple[int, list[str]]:
        completed = 0
        errors = []
        for plan in plans:
            if cancel and cancel.is_set():
                break
            try:
                self.move(plan, root, kind, cancel)
                completed += 1
            except CancelledError:
                break
            except (OSError, ValueError) as error:
                errors.append(f'{plan.source}: {error}')
            progress(completed)
        return completed, errors

    def undo(self, operation_id: int, cancel: Event | None = None) -> None:
        operation = self.database.operation(operation_id)
        if operation['status'] != 'completed' or operation['kind'] == 'restore':
            raise ValueError('Only completed moves can be restored.')
        source = Path(operation['destination'])
        destination = Path(operation['source'])
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('Original path is occupied; nothing was overwritten.')
        if sha256(source, cancel) != operation['fingerprint']:
            raise ValueError('The moved file has changed. Restore it manually.')
        root = Path(os.path.commonpath([source.parent, destination.parent]))
        self.move(MovePlan(source, destination), root, 'restore', cancel)
        self.database.finish_operation(operation_id, 'restored')


def duplicate_removal_plan(groups: list[list[FileRecord]], selected: set[Path], root: Path,
                           cancel: Event | None = None) -> list[MovePlan]:
    """Keep at least one verified copy; deletion is a reversible local quarantine."""
    if not selected:
        raise ValueError('Select duplicates to remove first.')
    known = {record.path for group in groups for record in group}
    if not selected <= known:
        raise ValueError('Selection contains an unknown file.')
    reserved: set[Path] = set()
    plans = []
    for group in groups:
        chosen = [record for record in group if record.path in selected]
        if not chosen:
            continue
        survivors = [record for record in group if record.path not in selected]
        if not survivors:
            raise ValueError('Keep at least one copy in every group.')
        expected = sha256(survivors[0].path, cancel)
        for record in chosen:
            if sha256(record.path, cancel) != expected:
                raise ValueError('Duplicates changed since the search. Search again.')
            destination = unique_destination(root / '.filemind-trash' / record.path.name, reserved)
            reserved.add(destination)
            plans.append(MovePlan(record.path, destination, destination.name != record.path.name))
    return plans
