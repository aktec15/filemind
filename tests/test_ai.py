import io
import json
from pathlib import Path
from threading import Event
import urllib.error

import pytest

import ai_settings
from ai_settings import AIError, Connection, validate_workspaces
from ai_advisor import (Advice, Candidate, YandexAdvisor, advice_plans, candidate_folders,
                        metadata_payload, validate_advice)
from database import Database
from file_organizer import FileOrganizer


@pytest.fixture
def root(tmp_path):
    path = tmp_path / 'files'
    path.mkdir()
    return path


@pytest.fixture
def workspaces():
    return [{'name': 'Альфа', 'description': 'Компания Альфа, проект Портал'},
            {'name': 'Бета', 'description': 'Компания Бета, проект Аналитика'}]


def write(root, name, content='data'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def metadata(workspaces):
    return {'folders': [{'id': 0, 'folder': 'app'}, {'id': 1, 'folder': 'misc'}],
            'workspaces': [dict(id=i, **workspace) for i, workspace in enumerate(workspaces)]}


def valid_answer():
    return {'suggestions': [
        {'folder_id': 0, 'workspace_id': 0, 'reason': 'Название Альфа'},
        {'folder_id': 1, 'workspace_id': -1, 'reason': 'Уточните организацию'}]}


def test_metadata_has_names_but_no_file_contents_or_absolute_paths(root, workspaces, tmp_path):
    write(root, 'Портал/package.json', 'PRIVATE_CONTENT_MUST_NOT_LEAVE')
    write(root, 'Портал/.env', 'SECRET=NO')
    write(root, 'Портал/.git/config', 'PRIVATE_GIT')
    outside = write(tmp_path, 'outside/token.txt', 'EXTERNAL_SECRET')
    (root / 'Портал/outside').symlink_to(outside.parent, target_is_directory=True)
    write(root, 'Портал/node_modules/dependency/file.js')
    (root / 'alias').symlink_to(outside.parent, target_is_directory=True)
    candidates = candidate_folders(root)
    assert len(candidates) == 1
    payload = metadata_payload(root, candidates, workspaces)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert 'package.json' in encoded
    for excluded in ['PRIVATE_', 'EXTERNAL_', str(root), str(tmp_path), '.env', '.git', 'node_modules', 'outside']:
        assert excluded not in encoded
    assert payload['folders'][0]['category'] == 'Projects'
    assert not (root / 'Projects').exists()  # Preparing data is read-only.


def test_already_categorized_projects_can_be_assigned_to_organizations(root):
    write(root, 'Projects/portal/package.json')
    write(root, 'Workspaces/Альфа/Projects/old/package.json')
    write(root, '.hidden/a.txt')
    candidates = candidate_folders(root)
    assert [item.source.relative_to(root).as_posix() for item in candidates] == ['Projects/portal']


def test_ai_plans_keep_organizations_separate_and_support_restore(root, tmp_path, workspaces):
    first = write(root, 'one/app/package.json', 'alpha')
    second = write(root, 'two/app/package.json', 'beta')
    uncertain = write(root, 'unknown/readme.txt')
    candidates = [Candidate(0, first.parent, 'Projects', ()), Candidate(1, second.parent, 'Projects', ()),
                  Candidate(2, uncertain.parent, 'Documents', ())]
    plans = advice_plans(root, candidates, {0: 0, 1: 1, 2: -1}, workspaces)
    assert len(plans) == 2
    assert plans[0].destination == root / 'Workspaces/Альфа/Projects/app'
    assert plans[1].destination == root / 'Workspaces/Бета/Projects/app'
    db = Database(tmp_path / 'db.sqlite')
    organizer = FileOrganizer(db)
    assert organizer.execute(plans, root) == (2, [])
    assert (plans[0].destination / 'package.json').read_text() == 'alpha'
    assert (plans[1].destination / 'package.json').read_text() == 'beta'
    assert uncertain.exists()
    assert all('Workspaces' not in plan.source.parts for plan in organizer.preview(root))
    organizer.undo(db.history()[0]['id'])
    assert second.read_text() == 'beta'
    assert (plans[0].destination / 'package.json').exists()


def test_ai_destination_conflict_does_not_merge_folders(root, workspaces):
    folder = write(root, 'app/package.json').parent
    write(root, 'Workspaces/Альфа/Projects/app/keep.txt', 'keep')
    plans = advice_plans(root, [Candidate(0, folder, 'Projects', ())], {0: 0}, workspaces)
    assert plans[0].destination.name == 'app (1)'
    assert plans[0].conflict
    assert folder.exists()


@pytest.mark.parametrize('name', ['../Other', 'A/B', 'A\\B', '..', '.', 'CON', 'NUL.txt', 'COM1', 'trailing.', ' spaces ', 'a:b'])
def test_workspace_names_cannot_supply_paths(name):
    with pytest.raises(AIError):
        validate_workspaces([{'name': name, 'description': 'description'}])


def test_workspace_names_unique_on_windows():
    with pytest.raises(AIError):
        validate_workspaces([{'name': 'Org', 'description': 'a'}, {'name': 'org', 'description': 'b'}])


def test_ai_destination_symlink_rejected(root, workspaces, tmp_path):
    folder = write(root, 'app/package.json').parent
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'Workspaces').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='Symbolic'):
        advice_plans(root, [Candidate(0, folder, 'Projects', ())], {0: 0}, workspaces)
    assert not list(outside.iterdir())


@pytest.mark.parametrize('mutation', ['unknown_folder', 'unknown_workspace', 'duplicate', 'missing', 'bool', 'bad_reason'])
def test_model_output_validated_locally(workspaces, mutation):
    answer = valid_answer()
    if mutation == 'unknown_folder':
        answer['suggestions'][0]['folder_id'] = 123
    elif mutation == 'unknown_workspace':
        answer['suggestions'][0]['workspace_id'] = 99
    elif mutation == 'duplicate':
        answer['suggestions'][1]['folder_id'] = 0
    elif mutation == 'missing':
        answer['suggestions'].pop()
    elif mutation == 'bool':
        answer['suggestions'][0]['workspace_id'] = True
    else:
        answer['suggestions'][0]['reason'] = []
    with pytest.raises(AIError):
        validate_advice(answer, metadata(workspaces))


class FakeResponse(io.BytesIO):
    pass


def mock_provider(monkeypatch, payload, check=None):
    class Opener:
        def open(self, request, timeout):
            if check:
                check(request, timeout)
            return FakeResponse(json.dumps(payload).encode())
    monkeypatch.setattr('ai_advisor.urllib.request.build_opener', lambda *args: Opener())


def provider_answer(answer, status='ALTERNATIVE_STATUS_FINAL'):
    return {'result': {'alternatives': [{'status': status, 'message': {'text': json.dumps(answer)}}]}}


def test_provider_request_and_uncertainty(monkeypatch, workspaces):
    def check(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == 'https://ai.api.cloud.yandex.net/foundationModels/v1/completion'
        assert request.get_header('Authorization') == 'Api-Key fake-key'
        assert request.get_header('X-data-logging-enabled') == 'false'
        assert 'fake-key' not in request.data.decode()
        assert 'jsonSchema' in payload
        assert timeout == 30
    mock_provider(monkeypatch, provider_answer(valid_answer()), check)
    connection = Connection('folder-id', 'yandexgpt/latest', 'fake-key')
    assert 'fake-key' not in repr(connection)
    result = YandexAdvisor(connection).suggest(metadata(workspaces))
    assert result == [Advice(0, 0, 'Название Альфа'), Advice(1, -1, 'Уточните организацию')]


@pytest.mark.parametrize('status', ['ALTERNATIVE_STATUS_TRUNCATED_FINAL', 'ALTERNATIVE_STATUS_CONTENT_FILTER'])
def test_incomplete_provider_answers_never_become_plans(monkeypatch, workspaces, status):
    mock_provider(monkeypatch, provider_answer(valid_answer(), status))
    with pytest.raises(AIError, match='did not complete'):
        YandexAdvisor(Connection('folder-id', 'model', 'fake-key')).suggest(metadata(workspaces))


@pytest.mark.parametrize('code', [301, 401, 403, 429, 500])
def test_provider_errors_are_sanitized(monkeypatch, workspaces, code):
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError('https://example.invalid', code, 'fake-key SECRET', {}, io.BytesIO(b'SECRET'))
    monkeypatch.setattr('ai_advisor.urllib.request.build_opener', lambda *args: Opener())
    with pytest.raises(AIError) as error:
        YandexAdvisor(Connection('folder-id', 'model', 'fake-key')).suggest(metadata(workspaces))
    assert 'SECRET' not in str(error.value)
    assert 'fake-key' not in str(error.value)


def test_cancel_before_network_does_not_send(monkeypatch, workspaces):
    def forbidden(*args):
        pytest.fail('Cancelled request reached the network')
    monkeypatch.setattr('ai_advisor.urllib.request.build_opener', forbidden)
    cancel = Event()
    cancel.set()
    from duplicate_finder import CancelledError
    with pytest.raises(CancelledError):
        YandexAdvisor(Connection('folder-id', 'model', 'fake-key')).suggest(metadata(workspaces), cancel)


def test_credentials_never_saved_in_config_or_database(monkeypatch, tmp_path):
    class Vault:
        def set_password(self, service, account, password):
            self.password = password
        def get_password(self, service, account):
            return self.password
    vault = Vault()
    monkeypatch.setattr(ai_settings, 'credential_backend', lambda: vault)
    monkeypatch.delenv('FILEMIND_YANDEX_API_KEY', raising=False)
    ai_settings.save_api_key('FAKE_PRIVATE_KEY')
    assert ai_settings.read_api_key() == 'FAKE_PRIVATE_KEY'
    db = Database(tmp_path / 'test.db')
    monkeypatch.setattr(ai_settings, 'LOCAL_CONFIG', tmp_path / 'config.json')
    db.set('ai_settings', {'folder_id': 'folder', 'model': 'model', 'workspaces': []})
    assert 'FAKE_PRIVATE_KEY' not in json.dumps(ai_settings.load_config(db))
    assert b'FAKE_PRIVATE_KEY' not in db.path.read_bytes()


def test_vault_failure_has_no_plaintext_fallback(monkeypatch, tmp_path):
    class Vault:
        def set_password(self, *args):
            raise OSError('PRIVATE_KEY in unsafe backend message')
    monkeypatch.setattr(ai_settings, 'credential_backend', lambda: Vault())
    with pytest.raises(AIError) as error:
        ai_settings.save_api_key('PRIVATE_KEY')
    assert 'PRIVATE_KEY' not in str(error.value)
    assert not list(tmp_path.iterdir())
