"""Connection settings, exact outbound metadata and editable AI recommendations."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QMessageBox, QAbstractItemView)

from ai_settings import AIError, Connection, save_api_key, validate_workspaces


class AISettingsDialog(QDialog):
    def __init__(self, config, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle('YandexGPT · AI settings')
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        for label, attribute, value in [('Yandex Cloud folder ID', 'folder_id', config['folder_id']),
                                        ('Model', 'model', config['model']), ('API key', 'api_key', '')]:
            layout.addWidget(QLabel(label))
            field = QLineEdit(value)
            setattr(self, attribute, field)
            layout.addWidget(field)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText('Leave empty to keep the key in the system credential vault')
        layout.addWidget(QLabel('Workspaces / organizations · one per line: Name | Description and distinguishing names'))
        self.workspaces = QPlainTextEdit()
        self.workspaces.setPlaceholderText('Организация А | Название компании, клиенты, названия проектов\nЛичное | Мои личные проекты и хобби')
        self.workspaces.setPlainText('\n'.join(f"{item['name']} | {item['description']}" for item in config['workspaces']))
        layout.addWidget(self.workspaces)
        note = QLabel('The key is stored in your system credential vault. AI requests are optional and billed by Yandex. '
                      'Before sending, you can inspect the folder names, file names and workspace descriptions. '
                      'File contents are not sent.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            workspaces = []
            for line in self.workspaces.toPlainText().splitlines():
                if not line.strip():
                    continue
                name, separator, description = line.partition('|')
                if not separator:
                    raise AIError('Use Name | Description for each workspace.')
                workspaces.append({'name': name.strip(), 'description': description.strip()})
            validate_workspaces(workspaces)
            config = {'folder_id': self.folder_id.text().strip(), 'model': self.model.text().strip(),
                      'workspaces': workspaces}
            Connection(config['folder_id'], config['model'], self.api_key.text().strip() or 'unchanged').validate()
            if self.api_key.text().strip():
                save_api_key(self.api_key.text())
            self.database.set('ai_settings', config)
        except (AIError, OSError) as error:
            QMessageBox.warning(self, 'AI settings', str(error))
            return
        self.api_key.clear()
        self.accept()


class MetadataDialog(QDialog):
    def __init__(self, metadata, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Review data sent to YandexGPT')
        self.resize(800, 560)
        layout = QVBoxLayout(self)
        label = QLabel('YandexGPT will receive exactly this folder metadata and workspace context, with a classification instruction. '
                       'No file contents or absolute paths are included. This is a paid API request. No folders move at this step.')
        label.setWordWrap(True)
        layout.addWidget(label)
        view = QPlainTextEdit(json.dumps(metadata, ensure_ascii=False, indent=2))
        view.setReadOnly(True)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Send to YandexGPT')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AdviceDialog(QDialog):
    def __init__(self, root, candidates, advice, workspaces, parent=None):
        super().__init__(parent)
        self.setWindowTitle('AI advice · choose organizations')
        self.resize(1000, 560)
        self.choices = {}
        layout = QVBoxLayout(self)
        label = QLabel('Review the suggested organization for each whole folder. Choose “Leave in place” for uncertain items. '
                       'The next screen shows exact destination paths and name conflicts. Folder contents remain together.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(len(candidates), 3)
        self.table.setHorizontalHeaderLabels(['Folder', 'Workspace / organization', 'AI explanation'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        by_id = {item.folder_id: item for item in advice}
        for row, candidate in enumerate(candidates):
            item = by_id[candidate.id]
            self.table.setItem(row, 0, QTableWidgetItem(str(candidate.source.relative_to(root))))
            combo = QComboBox()
            combo.addItem('Leave in place / needs clarification', -1)
            for index, workspace in enumerate(workspaces):
                combo.addItem(workspace['name'], index)
            combo.setCurrentIndex(combo.findData(item.workspace_id))
            self.choices[candidate.id] = combo
            self.table.setCellWidget(row, 1, combo)
            explanation = QTableWidgetItem(item.reason)
            explanation.setToolTip(item.reason)
            self.table.setItem(row, 2, explanation)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Review move plan')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_workspaces(self):
        return {key: combo.currentData() for key, combo in self.choices.items()}
