"""Exercise the configured provider using synthetic metadata only; no real files sent."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_advisor import YandexAdvisor
from ai_settings import AIError, connection_from, load_config


class Defaults:
    def get(self, key, default=None):
        return default


def main():
    metadata = {'workspaces': [
        {'id': 0, 'name': 'Организация Альфа', 'description': 'Компания Альфа. Проект Альфа Портал.'},
        {'id': 1, 'name': 'Организация Бета', 'description': 'Компания Бета. Проект Бета Аналитика.'}],
        'folders': [
            {'id': 0, 'folder': 'Альфа Портал', 'category': 'Projects', 'entries': ['package.json'], 'entries_truncated': False},
            {'id': 1, 'folder': 'Бета Аналитика', 'category': 'Projects', 'entries': ['pyproject.toml'], 'entries_truncated': False},
            {'id': 2, 'folder': 'Без названия', 'category': 'Projects', 'entries': ['main.py'], 'entries_truncated': False}]}
    advice = YandexAdvisor(connection_from(load_config(Defaults()))).suggest(metadata)
    assignments = {item.folder_id: item.workspace_id for item in advice}
    if assignments != {0: 0, 1: 1, 2: -1}:
        print('Provider connected and returned valid advice, but synthetic organization/uncertainty checks failed.')
        return 1
    print('YandexGPT live check passed: two organizations separated, ambiguous folder left for clarification. Only synthetic metadata sent.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except AIError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
