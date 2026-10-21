import os
from pathlib import Path
from threading import Event
from collections.abc import Callable
from models import FileRecord
from filesystem import is_link

class FileScanner:
    def scan(self, root: Path, cancel: Event | None = None,
             progress: Callable[[int], None] = lambda _: None) -> tuple[list[FileRecord], list[str]]:
        root = root.absolute()
        if is_link(root) or not root.is_dir():
            raise ValueError('Choose an existing folder that is not a symbolic link.')
        records: list[FileRecord] = []
        errors: list[str] = []
        def walk_error(error: OSError) -> None:
            errors.append(str(error))
        for directory, folders, files in os.walk(root, followlinks=False, onerror=walk_error):
            if cancel and cancel.is_set():
                break
            safe_folders = []
            for name in folders:
                try:
                    if name.casefold() != '.filemind-trash' and not is_link(Path(directory, name)):
                        safe_folders.append(name)
                except OSError as error:
                    errors.append(f'{Path(directory, name)}: {error}')
            folders[:] = safe_folders
            for name in files:
                if cancel and cancel.is_set():
                    break
                path = Path(directory, name)
                try:
                    if not is_link(path) and path.is_file():
                        records.append(FileRecord.from_path(path))
                        if len(records) % 100 == 0:
                            progress(len(records))
                except OSError as error:
                    errors.append(f'{path}: {error}')
        progress(len(records))
        return records, errors
