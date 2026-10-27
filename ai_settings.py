"""Yandex connection metadata; API keys live only in the OS credential vault."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys

LOCAL_CONFIG = Path(__file__).with_name('.filemind-ai.json')
SERVICE = 'FileMind.YandexGPT'
ACCOUNT = 'api_key'


class AIError(ValueError):
    """An error safe to display or log without provider responses or credentials."""


def credential_backend():
    try:
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
            return Keyring()
        if sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring
            return WinVaultKeyring()
        from keyring.backends.SecretService import Keyring
        return Keyring()
    except Exception:
        raise AIError('System credential storage is unavailable. Install requirements.txt and unlock your credential vault.') from None


def read_api_key() -> str:
    value = os.environ.get('FILEMIND_YANDEX_API_KEY')
    if value:
        return value
    try:
        return credential_backend().get_password(SERVICE, ACCOUNT) or ''
    except Exception:
        raise AIError('Cannot read the YandexGPT key. Unlock your system credential vault or enter a key in AI settings.') from None


def save_api_key(value: str) -> None:
    if not value.strip() or '\n' in value or '\r' in value:
        raise AIError('Enter a valid API key.')
    try:
        credential_backend().set_password(SERVICE, ACCOUNT, value.strip())
    except Exception:
        raise AIError('Cannot save the key in the system credential vault. No plaintext copy was saved.') from None


@dataclass(frozen=True)
class Connection:
    folder_id: str
    model: str
    api_key: str = field(repr=False)

    def validate(self):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{3,100}', self.folder_id):
            raise AIError('Enter the Yandex Cloud folder ID in AI settings.')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*', self.model):
            raise AIError('Enter a valid model name, for example yandexgpt/latest.')
        if not self.api_key or any(c in self.api_key for c in '\r\n'):
            raise AIError('Add a YandexGPT API key in AI settings.')


def load_config(database) -> dict:
    config = {'folder_id': '', 'model': 'yandexgpt/latest', 'workspaces': []}
    if LOCAL_CONFIG.is_file():
        try:
            bootstrap = json.loads(LOCAL_CONFIG.read_text(encoding='utf-8'))
            config.update({key: bootstrap[key] for key in ('folder_id', 'model') if key in bootstrap})
        except (OSError, ValueError, TypeError):
            raise AIError('Cannot read the local AI connection settings.') from None
    config.update(database.get('ai_settings', {}))
    config['folder_id'] = os.environ.get('FILEMIND_YANDEX_FOLDER_ID', config['folder_id'])
    config['model'] = os.environ.get('FILEMIND_YANDEX_MODEL', config['model'])
    return config


def connection_from(config: dict) -> Connection:
    connection = Connection(config['folder_id'].strip(), config['model'].strip(), read_api_key())
    connection.validate()
    return connection


def validate_workspaces(workspaces: list[dict]) -> None:
    if not workspaces or len(workspaces) > 20:
        raise AIError('Add between 1 and 20 workspaces (organizations) with descriptions.')
    seen = set()
    for workspace in workspaces:
        name, description = workspace.get('name', ''), workspace.get('description', '')
        # Portable single directory names, including Windows reserved names.
        if (not isinstance(name, str) or not name or len(name) > 80
                or name != name.strip() or name.endswith('.')
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name in {'.', '..'} or name.casefold() in seen
                or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
            raise AIError('Workspace names must be unique, valid folder names without slashes or reserved names.')
        if not isinstance(description, str) or not description.strip() or len(description) > 1500:
            raise AIError('Describe each workspace in 1–1500 characters so AI can distinguish organizations.')
        seen.add(name.casefold())
