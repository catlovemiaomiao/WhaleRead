"""User-driven folder authorization, safe preview import and explicit exports."""
from __future__ import annotations

from apple_services import choose_open_file, choose_save_file, choose_directory

import json
from pathlib import Path
import shutil
from PySide6.QtCore import QObject, Property, Signal, Slot, QCoreApplication
from PySide6.QtWidgets import QFileDialog
from bookshelf import Bookshelf
from reading_sessions import read_list
from legacy_import import import_settings


class DataSettings(QObject):
    changed = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._notice = ''
        self._legacy_key = None

    @Property(str, notify=changed)
    def taskFolder(self):  # noqa: N802
        return str(self.owner.workspace)

    @Property(str, notify=changed)
    def notice(self):
        return (self._notice() if callable(self._notice) else self._notice) or self.owner._storage_notice

    @Property(bool, notify=changed)
    def legacyKeyImported(self):  # noqa: N802
        return self._legacy_key is not None

    def _busy(self):
        return any(self.owner._provider_locked(f) for f in ('translation', 'review', 'ask'))

    def _fail(self):
        self._notice = lambda: QCoreApplication.translate('DataSettings', '操作未完成。请检查文件授权、格式或磁盘空间；已有书库与任务保留。')
        self.changed.emit()

    @Slot()
    def chooseTaskFolder(self):  # noqa: N802
        if self._busy():
            return
        folder = choose_directory(None, QCoreApplication.translate(
            'DataSettings', '选择任务目录（可选择预览版的旧目录）'), str(self.owner.workspace))
        if not folder:
            return
        try:
            folder = self.owner.file_access.selected(folder)
            roots = read_list(self.owner.settings, 'storage/task_roots_v1')
            if str(folder) not in roots:
                roots.append(str(folder))
            self.owner.settings.setValue('storage/task_roots_v1', json.dumps(roots, ensure_ascii=False))
            self.owner.settings.setValue('storage/workspace', str(folder))
            self.owner.workspace = folder
            self.owner.settings.remove('library/imported_existing_v2')
            self.owner.library.bootstrap(folder)
            self.owner.shelfChanged.emit()
            self.owner.changed.emit()
            self._notice = lambda: QCoreApplication.translate('DataSettings', '目录已授权，已有任务已加入书库；文件保留在原位置。新任务使用此目录。')
            self.changed.emit()
        except Exception:
            self._fail()

    @Slot()
    def authorizeBookFolder(self):  # noqa: N802
        folder = choose_directory(None, QCoreApplication.translate(
            'DataSettings', '重新授权旧书籍所在目录'), '')
        if not folder:
            return
        try:
            self.owner.file_access.selected(folder, read_only=True)
            self._notice = lambda: QCoreApplication.translate('DataSettings', '书籍目录已授权。可从书库重新打开原有书籍。')
            self.owner.changed.emit()
            self.changed.emit()
        except Exception:
            self._fail()

    @Slot()
    def importPreviewSettings(self):  # noqa: N802
        if self._busy():
            return
        path, _ = choose_open_file(None, QCoreApplication.translate(
            'DataSettings', '选择预览版偏好文件'), '', 'Preferences (*.plist *.ini)')
        if not path:
            return
        try:
            path = self.owner.file_access.selected(path, read_only=True)
            backup_directory = self.owner.data_root / 'MigrationBackups'
            report = import_settings(path, self.owner.settings, backup_directory)
            self.owner.library = Bookshelf(self.owner.settings, self.owner.storage_context)
            self.owner.providers.import_legacy_route()
            self.owner.shelfChanged.emit()
            self.owner.changed.emit()
            backup = report['backup']
            self._notice = lambda: QCoreApplication.translate('DataSettings', '旧书库与阅读记录已合并，并已备份导入前的设置。请重新授权旧任务或书籍目录；重启后恢复阅读窗口。') + '\n' + backup
            self.changed.emit()
        except Exception:
            self._fail()

    @Slot()
    def exportOutput(self):  # noqa: N802
        source = self.owner._output_target()
        if source is None or not source.is_file():
            return
        path, _ = choose_save_file(None, QCoreApplication.translate(
            'DataSettings', '导出译本副本'), source.name, 'Books (*.txt *.epub)')
        if not path:
            return
        try:
            # Save-panel access covers a newly created file; bookmark only
            # after it exists. Copy through a stream so a failed save cannot
            # alter the authoritative translation in its task directory.
            if Path(path).resolve() == source.resolve():
                raise ValueError('Export destination is the source')
            with source.open('rb') as incoming, Path(path).open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            self.owner.file_access.selected(path)
            self._notice = lambda: QCoreApplication.translate('DataSettings', '译本副本已导出。任务中的原译本保留。')
            self.changed.emit()
        except Exception:
            self._fail()

    @Slot()
    def importLegacyAskKey(self):  # noqa: N802
        if self._busy():
            return
        pid = self.owner.providers.selected('ask')
        if not self.owner.providers.contains(pid) or self.owner.providers.is_builtin(pid):
            self._fail()
            return
        path, _ = choose_open_file(None, QCoreApplication.translate(
            'DataSettings', '选择旧 ask-key.json（仅导入所选问 AI 模型）'), '', 'JSON (*.json)')
        if not path:
            return
        try:
            path = self.owner.file_access.selected(path)
            if path.stat().st_size > 16384:
                raise ValueError('Invalid legacy key file')
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or set(data) != {'key'} or not isinstance(data['key'], str) or not data['key'].strip():
                raise ValueError('Invalid legacy key file')
            row = self.owner.providers.route(pid)
            saved = self.owner.providers.save(pid, row['label'], row['api_base'], row['model'], data['key'], True)
            if not saved:
                raise ValueError('Could not save imported key')
            self.owner.providers.select('ask', saved)
            self._legacy_key = (path, saved)
            self._notice = lambda: QCoreApplication.translate('DataSettings', '旧密钥已存入并核验系统钥匙串。旧文件仍保留；可明确删除，旧预览版将需要重新输入密钥。')
            self.changed.emit()
        except Exception:
            self._fail()

    @Slot()
    def deleteMigratedKey(self):  # noqa: N802
        if not self._legacy_key:
            return
        try:
            path, pid = self._legacy_key
            data = json.loads(path.read_text())
            if set(data) != {'key'} or self.owner.providers.key(pid) != data['key']:
                raise ValueError('Credential no longer matches')
            path.unlink()
            self._legacy_key = None
            self._notice = lambda: QCoreApplication.translate('DataSettings', '已删除核验过的旧密钥文件。当前模型使用系统钥匙串。')
            self.changed.emit()
        except Exception:
            self._fail()
