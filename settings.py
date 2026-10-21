import os
import sys
from pathlib import Path

APP_NAME = 'FileMind'

def data_directory() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    result = base / APP_NAME
    result.mkdir(parents=True, exist_ok=True)
    return result
