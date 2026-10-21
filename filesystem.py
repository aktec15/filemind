"""Shared path checks, including Windows junctions and reparse points."""
from pathlib import Path
import stat


def is_link(path: Path) -> bool:
    attributes = path.lstat()
    return stat.S_ISLNK(attributes.st_mode) or bool(
        getattr(attributes, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
