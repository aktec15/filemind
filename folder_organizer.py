"""Classify whole folders and move them without replacing existing paths."""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from threading import Event

from duplicate_finder import CancelledError, sha256
from filesystem import is_link
from models import CATEGORIES, category_for

DESTINATIONS = {*CATEGORIES, 'Other', 'Projects', 'Folders', 'Workspaces'}
PROJECT_MARKERS = {'.git', 'pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod',
                   'setup.py', 'setup.cfg', 'CMakeLists.txt', 'pom.xml', 'build.gradle',
                   'build.gradle.kts', 'Gemfile', 'composer.json', 'requirements.txt'}


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise CancelledError('Operation cancelled.')


def is_project(names: set[str]) -> bool:
    return bool(names & PROJECT_MARKERS) or any(name.endswith('.sln') for name in names)


def folder_category(folder: Path, cancel: Event | None = None) -> str:
    categories = set()
    project = False
    def fail(error):
        raise error
    for directory, folders, files in os.walk(folder, followlinks=False, onerror=fail):
        check_cancel(cancel)
        if is_project(set(folders) | set(files)):
            project = True
            # Project internals cannot influence folder classification.
            folders[:] = []
            continue
        folders[:] = [name for name in folders if not is_link(Path(directory, name))]
        categories.update(category_for(Path(name)) for name in files)
    if project:
        return 'Projects'
    return next(iter(categories)) if len(categories) == 1 else 'Folders'


def folder_fingerprint(folder: Path, cancel: Event | None = None) -> str:
    """Include names, empty folders, permissions, file bytes and link targets.

    Links are recorded as links, never traversed. Timestamps are omitted because
    a directory rename can legitimately change metadata.
    """
    digest = hashlib.sha256()
    def visit(path):
        check_cancel(cancel)
        info = path.lstat()
        relative = path.relative_to(folder).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            entry = [relative, 'link', mode, os.readlink(path)]
        elif is_link(path):
            raise ValueError(f'Unsupported reparse point: {path}')
        elif stat.S_ISDIR(info.st_mode):
            entry = [relative, 'directory', mode]
        elif stat.S_ISREG(info.st_mode):
            entry = [relative, 'file', mode, sha256(path, cancel)]
        else:
            raise ValueError(f'Unsupported special file: {path}')
        digest.update(json.dumps(entry, ensure_ascii=True).encode() + b'\n')
        if entry[1] == 'directory':
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                visit(child)
    visit(folder)
    return 'directory-sha256:' + digest.hexdigest()


def rename_folder(source: Path, destination: Path) -> None:
    """Atomic no-replace rename. Cross-filesystem moves fail with source intact."""
    if os.name == 'nt':
        os.rename(source, destination)  # Windows refuses an existing destination.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 0x4)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise OSError(errno.ENOTSUP, 'Atomic folder moves are unsupported on this platform.')
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
