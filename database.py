import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS favorites (path TEXT PRIMARY KEY, added TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
                    destination TEXT NOT NULL, fingerprint TEXT NOT NULL, created TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '');
            ''')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def add_favorite(self, path: Path) -> None:
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)',
                       (str(path.absolute()), datetime.now(timezone.utc).isoformat()))

    def remove_favorite(self, path: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM favorites WHERE path=?', (path,))

    def favorites(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute('SELECT * FROM favorites ORDER BY added DESC').fetchall()

    def begin_operation(self, kind: str, source: Path, destination: Path, fingerprint: str) -> int:
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO operations
                (kind,source,destination,fingerprint,created) VALUES (?,?,?,?,?)''',
                (kind, str(source), str(destination), fingerprint,
                 datetime.now(timezone.utc).isoformat()))
            return int(cursor.lastrowid)

    def finish_operation(self, operation_id: int, status: str, error: str = '') -> None:
        with self.connect() as db:
            db.execute('UPDATE operations SET status=?,error=? WHERE id=?', (status, error, operation_id))

    def history(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute('SELECT * FROM operations ORDER BY id DESC').fetchall()

    def operation(self, operation_id: int) -> sqlite3.Row:
        with self.connect() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            raise ValueError('Operation no longer exists.')
        return row
