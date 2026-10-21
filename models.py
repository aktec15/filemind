from dataclasses import dataclass
from pathlib import Path

CATEGORIES = {
    'Documents': 'pdf docx doc txt xlsx xls pptx ppt odt rtf csv',
    'Images': 'png jpg jpeg gif bmp webp svg tif tiff heic',
    'Videos': 'mp4 avi mkv mov webm',
    'Audio': 'mp3 wav flac aac ogg m4a',
    'Archives': 'zip rar 7z tar gz bz2 xz',
    'Code': 'py js ts html css cpp h java go json yaml yml rs',
}

def category_for(path: Path) -> str:
    extension = path.suffix.lower().lstrip('.')
    return next((name for name, extensions in CATEGORIES.items()
                 if extension in extensions.split()), 'Other')

@dataclass(frozen=True)
class FileRecord:
    path: Path
    size: int
    modified: float
    category: str

    @classmethod
    def from_path(cls, path: Path) -> 'FileRecord':
        stat = path.stat()
        return cls(path.absolute(), stat.st_size, stat.st_mtime, category_for(path))

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()


def format_size(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:,.1f} {unit}'
        value /= 1024
    return str(size)
