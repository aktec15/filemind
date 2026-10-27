"""Import only Yandex connection fields from an explicitly selected local .env."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_settings import AIError, Connection, LOCAL_CONFIG, save_api_key


def import_connection(source: Path):
    values = {}
    for line in source.read_text(encoding='utf-8').splitlines():
        key, separator, value = line.strip().removeprefix('export ').partition('=')
        if separator and key.strip() in {'YANDEX_FOLDER_ID', 'YANDEX_MODEL', 'YANDEX_API_KEY'}:
            tokens = shlex.split(value, comments=True)
            values[key.strip()] = tokens[0] if tokens else ''
    connection = Connection(values.get('YANDEX_FOLDER_ID', ''),
                            values.get('YANDEX_MODEL', 'yandexgpt/latest'),
                            values.get('YANDEX_API_KEY', ''))
    connection.validate()
    save_api_key(connection.api_key)
    descriptor = os.open(LOCAL_CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump({'folder_id': connection.folder_id, 'model': connection.model}, stream)
    print('Yandex connection imported. API key stored in the system credential vault; no secret written to project files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    try:
        import_connection(args.source)
    except (AIError, OSError, ValueError):
        print('Import failed. Check the source connection settings and unlock the system credential vault.', file=sys.stderr)
        sys.exit(1)
