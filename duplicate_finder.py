import hashlib
from collections import defaultdict
from pathlib import Path
from threading import Event
from collections.abc import Callable
from models import FileRecord
from filesystem import is_link

class CancelledError(Exception):
    pass

def sha256(path: Path, cancel: Event | None = None) -> str:
    if is_link(path):
        raise ValueError(f'Symbolic links are not supported: {path}')
    digest = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel and cancel.is_set():
                raise CancelledError('Operation cancelled.')
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise OSError(f'File changed while reading: {path}')
    return digest.hexdigest()

class DuplicateFinder:
    def find(self, records: list[FileRecord], cancel: Event | None = None,
             progress: Callable[[int], None] = lambda _: None) -> tuple[list[list[FileRecord]], list[str]]:
        sizes: dict[int, list[FileRecord]] = defaultdict(list)
        for record in records:
            sizes[record.size].append(record)
        hashes: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
        errors = []
        count = 0
        for candidates in sizes.values():
            if len(candidates) < 2:
                continue
            for record in candidates:
                if cancel and cancel.is_set():
                    raise CancelledError('Duplicate search cancelled.')
                try:
                    digest = sha256(record.path, cancel)
                    hashes[(record.path.stat().st_size, digest)].append(record)
                except (OSError, ValueError) as error:
                    errors.append(f'{record.path}: {error}')
                count += 1
                progress(count)
        return [group for group in hashes.values() if len(group) > 1], errors
