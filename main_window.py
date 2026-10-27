import os
import sys
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QDate, QUrl, QTimer, QSize
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QListWidget, QTableView, QHeaderView, QFileDialog,
    QMessageBox, QProgressBar, QToolBar, QMenu, QDialog, QDialogButtonBox,
    QComboBox, QDoubleSpinBox, QDateEdit, QCheckBox, QAbstractItemView,
    QTableWidget, QTableWidgetItem, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QSplitter, QStyle,
)
from database import Database
from models import FileRecord, CATEGORIES, format_size
from file_scanner import FileScanner
from duplicate_finder import DuplicateFinder
from file_organizer import FileOrganizer, duplicate_removal_plan
from file_search import SearchFilters
from workers import Worker
from ai_settings import AIError, load_config, connection_from, validate_workspaces
from ai_advisor import candidate_folders, metadata_payload, YandexAdvisor, advice_plans
from ai_dialogs import AISettingsDialog, MetadataDialog, AdviceDialog

class FileTableModel(QAbstractTableModel):
    headers = ['Name', 'Extension', 'Size', 'Modified', 'Path']
    def __init__(self):
        super().__init__()
        self.records: list[FileRecord] = []

    def set_records(self, records):
        self.beginResetModel()
        self.records = records
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self.records[index.row()]
        values = [record.path.name, record.extension, record.size, record.modified, str(record.path)]
        if role == Qt.UserRole:
            return values[index.column()]
        if role == Qt.ToolTipRole:
            return str(record.path)
        if role == Qt.DisplayRole:
            if index.column() == 2:
                return format_size(record.size)
            if index.column() == 3:
                return datetime.fromtimestamp(record.modified).strftime('%d %b %Y  %H:%M')
            return values[index.column()]

class FileProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.filters = SearchFilters()
        self.setSortRole(Qt.UserRole)

    def filterAcceptsRow(self, row, parent):
        return self.filters.matches(self.sourceModel().records[row])

class MoveTableModel(QAbstractTableModel):
    def __init__(self, plans, parent=None):
        super().__init__(parent)
        self.plans = plans

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.plans)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return ['Source', 'Destination', 'Details'][section]

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.ToolTipRole):
            plan = self.plans[index.row()]
            return [str(plan.source), str(plan.destination),
                    ('Whole folder' if plan.is_directory else 'File') + ('; renamed safely' if plan.conflict else '')][index.column()]

class PreviewDialog(QDialog):
    def __init__(self, plans, parent):
        super().__init__(parent)
        self.setWindowTitle('Review folder and file moves')
        self.resize(920, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'{len(plans):,} items will be moved. Folders keep their complete contents. Existing paths will never be overwritten.'))
        table = QTableView()
        table.setModel(MoveTableModel(plans, table))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(table)
        layout.addWidget(QLabel('Moved projects may need their virtual environments recreated.'))
        layout.addWidget(QLabel('Cancel stops after the current safe step. Completed moves can be restored from History.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Confirm moves')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

class MainWindow(QMainWindow):
    sections = ['Overview', 'All Files', 'Documents', 'Images', 'Videos', 'Audio',
                'Archives', 'Duplicates', 'Favorites', 'History']

    def __init__(self, database: Database):
        super().__init__()
        self.db = database
        self.organizer = FileOrganizer(database)
        self.root: Path | None = None
        self.records = []
        self.groups = []
        self.worker = None
        self.section = 'Overview'
        self.setWindowTitle('FileMind — Your files, in order')
        self.resize(*self.db.get('window_size', [1100, 700]))
        self.setMinimumSize(850, 560)
        self.build_ui()
        self.build_actions()
        self.apply_theme()
        self.refresh_favorites()
        self.refresh_history()
        missing = [row['path'] for row in self.db.favorites() if not Path(row['path']).is_dir()]
        if missing:
            QTimer.singleShot(0, lambda: self.inform('Unavailable favorites', '\n'.join(missing)))

    def build_ui(self):
        splitter = QSplitter()
        self.setCentralWidget(splitter)
        sidebar = QWidget()
        sidebar.setMinimumWidth(185)
        sidebar.setMaximumWidth(250)
        side = QVBoxLayout(sidebar)
        brand = QLabel('◈  FileMind')
        brand.setStyleSheet('font-size: 23px; font-weight: 600; padding: 18px 8px;')
        side.addWidget(brand)
        self.navigation = QListWidget()
        self.navigation.addItems(self.sections)
        self.navigation.setCurrentRow(0)
        self.navigation.currentTextChanged.connect(self.navigate)
        side.addWidget(self.navigation)
        local = QLabel('FILES STAY LOCAL\nOptional AI advice sends reviewed metadata')
        local.setObjectName('subtitle')
        local.setWordWrap(True)
        side.addWidget(local)
        splitter.addWidget(sidebar)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 22, 24, 16)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        self.title = QLabel('Overview')
        self.title.setObjectName('title')
        heading.addWidget(self.title)
        heading.addStretch()
        self.open_button = QPushButton('Open folder')
        self.open_button.setObjectName('primary')
        self.open_button.setToolTip('Choose a folder to scan recursively')
        self.open_button.clicked.connect(lambda: self.open_folder())
        heading.addWidget(self.open_button)
        self.favorite_button = QPushButton('☆ Save folder')
        self.favorite_button.setToolTip('Add the current folder to Favorites')
        self.favorite_button.clicked.connect(self.add_favorite)
        heading.addWidget(self.favorite_button)
        layout.addLayout(heading)
        self.folder_label = QLabel('Choose a folder to start exploring your files.')
        self.folder_label.setObjectName('subtitle')
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        self.summary = QLabel('0 files  ·  0 B')
        layout.addWidget(self.summary)
        self.search_panel = QWidget()
        filters_layout = QGridLayout(self.search_panel)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search by file name…')
        self.search.textChanged.connect(self.apply_filters)
        self.extension = QLineEdit()
        self.extension.setPlaceholderText('Extension, e.g. pdf')
        self.extension.textChanged.connect(self.apply_filters)
        self.category = QComboBox()
        self.category.addItems(['All', *CATEGORIES, 'Other'])
        self.category.currentTextChanged.connect(self.apply_filters)
        clear = QPushButton('Clear filters')
        clear.clicked.connect(self.clear_filters)
        filters_layout.addWidget(self.search, 0, 0, 1, 3)
        filters_layout.addWidget(self.extension, 0, 3)
        filters_layout.addWidget(self.category, 0, 4)
        filters_layout.addWidget(clear, 0, 5)
        self.minimum = QDoubleSpinBox()
        self.maximum = QDoubleSpinBox()
        for spin in (self.minimum, self.maximum):
            spin.setRange(0, 1_000_000_000)
            spin.setSuffix(' MB')
            spin.setDecimals(3)
            spin.valueChanged.connect(self.apply_filters)
        self.maximum.setSpecialValueText('No maximum')
        self.minimum.setToolTip('Minimum file size in MB (1 MB = 1,048,576 bytes)')
        self.maximum.setToolTip('Maximum file size in MB; zero means no limit')
        self.date_enabled = QCheckBox('Modified between')
        self.date_enabled.toggled.connect(self.apply_filters)
        self.date_start = QDateEdit(QDate.currentDate().addYears(-1))
        self.date_end = QDateEdit(QDate.currentDate())
        for date in (self.date_start, self.date_end):
            date.setCalendarPopup(True)
            date.dateChanged.connect(self.apply_filters)
        filters_layout.addWidget(QLabel('Minimum size'), 1, 0)
        filters_layout.addWidget(self.minimum, 1, 1)
        filters_layout.addWidget(self.maximum, 1, 2)
        filters_layout.addWidget(self.date_enabled, 1, 3)
        filters_layout.addWidget(self.date_start, 1, 4)
        filters_layout.addWidget(self.date_end, 1, 5)
        layout.addWidget(self.search_panel)
        self.stack = QStackedWidget()
        self.model = FileTableModel()
        self.proxy = FileProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 160)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.file_menu)
        self.table.doubleClicked.connect(lambda index: self.open_file(self.record_at(index).path))
        self.stack.addWidget(self.table)
        self.duplicates_page = QWidget()
        duplicates_layout = QVBoxLayout(self.duplicates_page)
        duplicates_layout.setContentsMargins(0, 0, 0, 0)
        duplicate_buttons = QHBoxLayout()
        find = QPushButton('Find duplicates')
        find.clicked.connect(self.find_duplicates)
        remove = QPushButton('Remove selected copies…')
        remove.setToolTip('Move checked copies to local quarantine; restore them from History')
        remove.clicked.connect(self.remove_duplicates)
        duplicate_buttons.addWidget(find)
        duplicate_buttons.addWidget(remove)
        duplicate_buttons.addStretch()
        duplicates_layout.addLayout(duplicate_buttons)
        self.duplicate_tree = QTreeWidget()
        self.duplicate_tree.setHeaderLabels(['Name / group', 'Size', 'Modified', 'Path'])
        self.duplicate_tree.setColumnWidth(0, 240)
        self.duplicate_tree.setSortingEnabled(True)
        self.duplicate_tree.itemDoubleClicked.connect(self.duplicate_open)
        self.duplicate_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.duplicate_tree.customContextMenuRequested.connect(self.duplicate_menu)
        duplicates_layout.addWidget(self.duplicate_tree)
        self.stack.addWidget(self.duplicates_page)
        self.favorites_page = QWidget()
        favorites_layout = QVBoxLayout(self.favorites_page)
        self.favorite_list = QListWidget()
        self.favorite_list.itemDoubleClicked.connect(lambda item: self.open_folder(Path(item.data(Qt.UserRole))))
        favorites_layout.addWidget(QLabel('Double-click a folder to scan it.'))
        favorites_layout.addWidget(self.favorite_list)
        remove_favorite = QPushButton('Remove selected favorite')
        remove_favorite.clicked.connect(self.remove_favorite)
        favorites_layout.addWidget(remove_favorite)
        self.stack.addWidget(self.favorites_page)
        self.history_page = QWidget()
        history_layout = QVBoxLayout(self.history_page)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(['Date', 'Action / status', 'Source', 'Destination', 'Details'])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        history_layout.addWidget(self.history_table)
        restore = QPushButton('Restore selected move…')
        restore.clicked.connect(self.restore_selected)
        history_layout.addWidget(restore)
        self.stack.addWidget(self.history_page)
        layout.addWidget(self.stack, 1)
        self.result_label = QLabel('No folder selected')
        layout.addWidget(self.result_label)
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.hide()
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.cancel_button)
        layout.addLayout(bottom)
        splitter.addWidget(content)
        splitter.setSizes([210, 890])
        splitter.setStretchFactor(1, 1)
        self.statusBar().showMessage('Ready · AI advice is optional')

    def build_actions(self):
        file_menu = self.menuBar().addMenu('File')
        tools_menu = self.menuBar().addMenu('Tools')
        help_menu = self.menuBar().addMenu('Help')
        toolbar = QToolBar('Quick actions')
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        entries = [
            (file_menu, 'Open Folder', lambda: self.open_folder(), QStyle.SP_DirOpenIcon, 'Ctrl+O'),
            (file_menu, 'Rescan Folder', self.rescan, QStyle.SP_BrowserReload, 'F5'),
            (tools_menu, 'Organize Folders', self.organize, QStyle.SP_DirIcon, ''),
            (tools_menu, 'AI Advisor', self.ai_advise, QStyle.SP_MessageBoxInformation, ''),
            (tools_menu, 'Find Duplicates', self.find_duplicates, QStyle.SP_FileDialogContentsView, ''),
            (tools_menu, 'Search Files', self.focus_search, QStyle.SP_FileDialogDetailedView, 'Ctrl+F'),
        ]
        self.task_actions = []
        for menu, title, callback, icon, shortcut in entries:
            action = QAction(self.style().standardIcon(icon), title, self)
            action.setToolTip(title)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(callback)
            menu.addAction(action)
            toolbar.addAction(action)
            self.task_actions.append(action)
        settings = tools_menu.addAction('Settings')
        settings.triggered.connect(self.show_settings)
        tools_menu.addAction('AI settings', self.show_ai_settings)
        file_menu.addSeparator()
        file_menu.addAction('Exit', self.close)
        help_menu.addAction('About FileMind', lambda: self.inform('About FileMind',
            'FileMind 1.0\nLocal file organizer for Windows 10/11.\n'
            'SHA-256 duplicate detection · SQLite history\nOptional YandexGPT advice sends reviewed folder metadata.'))

    def inform(self, title, message):
        QMessageBox.information(self, title, message)

    def report_error(self, message):
        QMessageBox.warning(self, 'FileMind', message)

    def apply_theme(self):
        if self.db.get('theme', 'Dark') == 'Dark':
            self.setStyleSheet(Path(__file__).with_name('styles.qss').read_text(encoding='utf-8'))
        else:
            self.setStyleSheet('QLabel#title { font-size: 27px; font-weight: 600; }')

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Settings')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Theme'))
        theme = QComboBox()
        theme.addItems(['Dark', 'Light'])
        theme.setCurrentText(self.db.get('theme', 'Dark'))
        layout.addWidget(theme)
        layout.addWidget(QLabel('Recursive scanning is enabled. Symbolic links and quarantine are skipped.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.db.set('theme', theme.currentText())
            self.apply_theme()

    def busy(self):
        return self.worker is not None

    def show_ai_settings(self):
        if self.busy():
            return
        try:
            AISettingsDialog(load_config(self.db), self.db, self).exec()
        except AIError as error:
            self.report_error(str(error))

    def when_idle(self, callback):
        if self.busy():
            QTimer.singleShot(25, lambda: self.when_idle(callback))
        else:
            callback()

    def ai_advise(self):
        if self.busy():
            return
        if not self.root:
            self.open_folder()
            return
        try:
            config = load_config(self.db)
            if not config['workspaces']:
                if AISettingsDialog(config, self.db, self).exec() != QDialog.Accepted:
                    return
                config = load_config(self.db)
            validate_workspaces(config['workspaces'])
        except AIError as error:
            self.report_error(str(error))
            return
        root = self.root
        workspaces = config['workspaces']
        def ready(candidates):
            self.when_idle(lambda: self.send_ai_metadata(root, candidates, workspaces, config))
        self.run_task('Preparing AI folder metadata',
                      lambda cancel, progress: candidate_folders(root, cancel), ready)

    def send_ai_metadata(self, root, candidates, workspaces, config):
        if self.root != root:
            return
        if not candidates:
            self.inform('AI Advisor', 'No folders to classify. Folders already in Workspaces stay in place.')
            return
        try:
            metadata = metadata_payload(root, candidates, workspaces)
            if MetadataDialog(metadata, self).exec() != QDialog.Accepted:
                return
            connection = connection_from(config)
        except AIError as error:
            self.report_error(str(error))
            return
        def ready(advice):
            self.when_idle(lambda: self.review_ai_advice(root, candidates, advice, workspaces))
        self.run_task('Asking YandexGPT · no folders are moving',
                      lambda cancel, progress: YandexAdvisor(connection).suggest(metadata, cancel), ready)

    def review_ai_advice(self, root, candidates, advice, workspaces):
        if self.root != root:
            return
        dialog = AdviceDialog(root, candidates, advice, workspaces, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            plans = advice_plans(root, candidates, dialog.selected_workspaces(), workspaces)
        except (OSError, ValueError) as error:
            self.report_error(str(error))
            return
        if not plans:
            self.inform('AI Advisor', 'All folders were left in place.')
            return
        if PreviewDialog(plans, self).exec() == QDialog.Accepted:
            self.run_task('Organizing folders by workspace', lambda cancel, progress:
                          self.organizer.execute(plans, root, cancel, progress), self.moves_done)

    def run_task(self, title, task, callback):
        if self.busy():
            return
        self.statusBar().showMessage(title)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.show()
        for action in self.task_actions:
            action.setEnabled(False)
        self.open_button.setEnabled(False)
        worker = Worker(task, self)
        self.worker = worker
        worker.progress.connect(lambda count: self.statusBar().showMessage(f'{title} · {count:,} processed'))
        worker.result.connect(callback)
        worker.error.connect(self.report_error)
        worker.finished.connect(self.task_finished)
        worker.start()

    def task_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.progress.hide()
        self.cancel_button.hide()
        self.cancel_button.setEnabled(True)
        self.open_button.setEnabled(True)
        for action in self.task_actions:
            action.setEnabled(True)
        self.refresh_history()
        self.statusBar().showMessage('Ready')

    def cancel_task(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.statusBar().showMessage('Cancelling safely…')

    def open_folder(self, path=None):
        if self.busy():
            return
        if path is None:
            chosen = QFileDialog.getExistingDirectory(self, 'Open folder', self.db.get('last_folder', str(Path.home())))
            if not chosen:
                return
            path = Path(chosen)
        path = Path(path).absolute()
        if not path.is_dir() or path.is_symlink():
            self.report_error('This folder is unavailable or is a symbolic link.')
            return
        self.root = path
        self.db.set('last_folder', str(path))
        self.folder_label.setText(str(path))
        self.records = []
        self.model.set_records([])
        self.groups = []
        self.duplicate_tree.clear()
        self.navigation.setCurrentRow(1)
        self.clear_filters()
        self.rescan()

    def rescan(self):
        if self.root and not self.busy():
            root = self.root
            self.run_task('Scanning folder', lambda cancel, progress: FileScanner().scan(root, cancel, progress), self.scan_done)

    def scan_done(self, result):
        self.records, errors = result
        self.groups = []
        self.duplicate_tree.clear()
        self.model.set_records(self.records)
        self.update_summary()
        self.apply_filters()
        if errors:
            self.report_error(f'{len(errors)} files or folders could not be read.\n' + '\n'.join(errors[:10]))

    def update_summary(self):
        total = sum(record.size for record in self.records)
        counts = {category: sum(r.category == category for r in self.records) for category in CATEGORIES}
        self.summary.setText(f'{len(self.records):,} files  ·  {format_size(total)}  ·  ' +
                             '  ·  '.join(f'{key}: {value}' for key, value in counts.items() if value))
        self.summary.setWordWrap(True)

    def navigate(self, section):
        self.section = section
        self.title.setText(section)
        self.search_panel.setVisible(section not in ('Favorites', 'History', 'Duplicates'))
        if section == 'Duplicates':
            self.stack.setCurrentIndex(1)
        elif section == 'Favorites':
            self.refresh_favorites()
            self.stack.setCurrentIndex(2)
        elif section == 'History':
            self.refresh_history()
            self.stack.setCurrentIndex(3)
        else:
            self.stack.setCurrentIndex(0)
            self.category.setCurrentText(section if section in CATEGORIES else 'All')
            self.apply_filters()

    def focus_search(self):
        self.navigation.setCurrentRow(1)
        self.search.setFocus()

    def clear_filters(self):
        self.search.clear()
        self.extension.clear()
        self.category.setCurrentText(self.section if self.section in CATEGORIES else 'All')
        self.minimum.setValue(0)
        self.maximum.setValue(0)
        self.date_enabled.setChecked(False)
        self.apply_filters()

    def apply_filters(self):
        after = before = None
        if self.date_enabled.isChecked():
            after = datetime.combine(self.date_start.date().toPython(), time.min).timestamp()
            before = datetime.combine(self.date_end.date().toPython() + timedelta(days=1), time.min).timestamp()
        self.proxy.filters = SearchFilters(self.search.text(), self.extension.text(), self.category.currentText(),
            int(self.minimum.value() * 1024 * 1024),
            int(self.maximum.value() * 1024 * 1024) if self.maximum.value() else None, after, before)
        self.proxy.invalidate()
        self.result_label.setText(f'{self.proxy.rowCount():,} of {len(self.records):,} files shown')

    def record_at(self, index):
        return self.records[self.proxy.mapToSource(index).row()]

    def open_file(self, path):
        if path.exists():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.report_error('Could not open this file with its default application.')
        else:
            self.report_error('This file no longer exists. Rescan the folder.')

    def reveal(self, path):
        if sys.platform == 'win32':
            subprocess.Popen(['explorer.exe', '/select,', str(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def file_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        path = self.record_at(index).path
        menu = QMenu(self)
        menu.addAction('Open file', lambda: self.open_file(path))
        menu.addAction('Show in folder', lambda: self.reveal(path))
        menu.addAction('Copy path', lambda: self.copy_path(path))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def copy_path(self, path):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(str(path))

    def organize(self):
        if self.busy():
            return
        if not self.root:
            self.open_folder()
            return
        root = self.root
        self.run_task('Preparing folder preview', lambda cancel, progress: self.organizer.preview(root, cancel), self.preview_ready)

    def preview_ready(self, plans):
        # Result arrives before QThread.finished; queue the dialog for the next event turn.
        QTimer.singleShot(0, lambda: self.show_preview_when_ready(plans))

    def show_preview_when_ready(self, plans):
        if self.busy():
            QTimer.singleShot(25, lambda: self.show_preview_when_ready(plans))
            return
        if not plans:
            self.inform('Organize folders', 'No folders to move. Loose files, hidden folders and category folders stay in place.')
            return
        if PreviewDialog(plans, self).exec() == QDialog.Accepted:
            root = self.root
            self.run_task('Organizing folders', lambda cancel, progress:
                          self.organizer.execute(plans, root, cancel, progress), self.moves_done)

    def moves_done(self, result):
        count, errors = result
        self.inform('Operation complete', f'{count:,} items moved.\nCompleted moves can be restored from History.' +
                    (f'\n{len(errors)} errors:\n' + '\n'.join(errors[:8]) if errors else ''))
        QTimer.singleShot(100, self.rescan_when_ready)

    def rescan_when_ready(self):
        if self.busy():
            QTimer.singleShot(50, self.rescan_when_ready)
        else:
            self.rescan()

    def find_duplicates(self):
        if self.busy():
            return
        if not self.root:
            self.open_folder()
            return
        self.navigation.setCurrentRow(7)
        records = list(self.records)
        self.run_task('Comparing file contents', lambda cancel, progress:
                      DuplicateFinder().find(records, cancel, progress), self.duplicates_done)

    def duplicates_done(self, result):
        self.groups, errors = result
        self.duplicate_tree.clear()
        self.duplicate_tree.setSortingEnabled(False)
        self.duplicate_tree.setUpdatesEnabled(False)
        wasted = 0
        for number, group in enumerate(self.groups, 1):
            wasted += sum(record.size for record in group[1:])
            parent = QTreeWidgetItem([f'Group {number} · {len(group)} copies', format_size(group[0].size), '', ''])
            self.duplicate_tree.addTopLevelItem(parent)
            for record in group:
                item = QTreeWidgetItem([record.path.name, format_size(record.size),
                    datetime.fromtimestamp(record.modified).strftime('%Y-%m-%d %H:%M'), str(record.path)])
                item.setData(0, Qt.UserRole, str(record.path))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Unchecked)
                parent.addChild(item)
            parent.setExpanded(True)
        self.duplicate_tree.setUpdatesEnabled(True)
        self.duplicate_tree.setSortingEnabled(True)
        self.result_label.setText(f'{len(self.groups)} duplicate groups · {format_size(wasted)} reclaimable')
        if errors:
            self.report_error('\n'.join(errors[:10]))

    def duplicate_open(self, item, column):
        path = item.data(0, Qt.UserRole)
        if path:
            self.open_file(Path(path))

    def duplicate_menu(self, position):
        item = self.duplicate_tree.itemAt(position)
        if not item or not item.data(0, Qt.UserRole):
            return
        path = Path(item.data(0, Qt.UserRole))
        menu = QMenu(self)
        menu.addAction('Open file', lambda: self.open_file(path))
        menu.addAction('Show in folder', lambda: self.reveal(path))
        menu.exec(self.duplicate_tree.viewport().mapToGlobal(position))

    def remove_duplicates(self):
        if self.busy() or not self.root:
            return
        selected = set()
        for i in range(self.duplicate_tree.topLevelItemCount()):
            group = self.duplicate_tree.topLevelItem(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if item.checkState(0) == Qt.Checked:
                    selected.add(Path(item.data(0, Qt.UserRole)))
        groups, root = self.groups, self.root
        self.run_task('Verifying selected duplicates', lambda cancel, progress:
                      duplicate_removal_plan(groups, selected, root, cancel), self.removal_ready)

    def removal_ready(self, plans):
        QTimer.singleShot(0, lambda: self.confirm_removal_when_ready(plans))

    def confirm_removal_when_ready(self, plans):
        if self.busy():
            QTimer.singleShot(25, lambda: self.confirm_removal_when_ready(plans))
            return
        if PreviewDialog(plans, self).exec() != QDialog.Accepted:
            return
        answer = QMessageBox.warning(self, 'Confirm duplicate removal',
            f'Remove {len(plans)} selected copies from their folders?\n'
            'They will be moved into .filemind-trash inside the selected folder.\n'
            'Space is not freed until you delete that quarantine manually. Restore via History.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            root, groups = self.root, self.groups
            selected = {plan.source for plan in plans}
            def remove(cancel, progress):
                # Verify survivors again after the confirmation dialog.
                checked = duplicate_removal_plan(groups, selected, root, cancel)
                if checked != plans:
                    raise ValueError('Destination paths changed. Review the selection again.')
                return self.organizer.execute(plans, root, cancel, progress, 'quarantine')
            self.run_task('Removing selected duplicates', remove, self.moves_done)

    def add_favorite(self):
        if self.root:
            self.db.add_favorite(self.root)
            self.refresh_favorites()
            self.statusBar().showMessage('Folder saved to Favorites')

    def refresh_favorites(self):
        self.favorite_list.clear()
        for row in self.db.favorites():
            path = row['path']
            self.favorite_list.addItem(path + ('  [unavailable]' if not Path(path).is_dir() else ''))
            self.favorite_list.item(self.favorite_list.count() - 1).setData(Qt.UserRole, path)

    def remove_favorite(self):
        item = self.favorite_list.currentItem()
        if item:
            self.db.remove_favorite(item.data(Qt.UserRole))
            self.refresh_favorites()

    def refresh_history(self):
        history = self.db.history()
        self.history_table.setRowCount(len(history))
        for index, row in enumerate(history):
            values = [row['created'][:19].replace('T', ' ') + ' UTC', f"{row['kind']} / {row['status']}",
                      row['source'], row['destination'], row['error']]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row['id'])
                item.setToolTip(value)
                self.history_table.setItem(index, column, item)

    def restore_selected(self):
        if self.busy():
            return
        row = self.history_table.currentRow()
        if row < 0:
            return
        operation_id = self.history_table.item(row, 0).data(Qt.UserRole)
        operation = self.db.operation(operation_id)
        if operation['status'] != 'completed' or operation['kind'] == 'restore':
            self.report_error('Select a completed organize or quarantine operation.')
            return
        answer = QMessageBox.question(self, 'Restore item',
            f"Move this item back?\n{operation['destination']}\n→ {operation['source']}\nExisting files will not be overwritten.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.run_task('Restoring item', lambda cancel, progress: self.organizer.undo(operation_id, cancel),
                          lambda _: QTimer.singleShot(100, self.rescan_when_ready))

    def closeEvent(self, event):
        if self.busy():
            self.cancel_task()
            self.statusBar().showMessage('Waiting for the task to stop safely. Close the window again when ready.')
            event.ignore()
            return
        self.db.set('window_size', [self.width(), self.height()])
        event.accept()
