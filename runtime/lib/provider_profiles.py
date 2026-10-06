"""Immutable routes, separate feature selections and explicit sharing consent."""
from __future__ import annotations

import hashlib
import json
import uuid
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Property, Signal, Slot, QCoreApplication
from credential_store import KeychainCredentialStore, CredentialError
from provider_transport import normalize_base, is_loopback


LOCAL_API_BASE = 'http://127.0.0.1:11434/v1'
DEFAULT_LOCAL_PROFILE = 'local_7b'
# Keep the preview's IDs and Ollama tags: saved editions bind to these routes.
LOCAL_PROFILES = {
    pid: dict(id=pid, label=label, api_base=LOCAL_API_BASE, model=model,
              auth_required=False, key_saved=False, file='~/Models/hy-mt2/' + filename)
    for pid, label, model, filename in (
        ('local_7b', 'Hy-MT2 7B · Ollama', 'jingdu-hy-mt2:7b-q4', 'Hy-MT2-7B-Q4_K_M.gguf'),
        ('local_1_8b', 'Hy-MT2 1.8B · Ollama', 'jingdu-hy-mt2:1.8b-q8', 'Hy-MT2-1.8B-Q8_0.gguf'),
    )
}
FEATURE_KEYS = dict(translation='translation/profile', review='review/profile', ask='ask/profile', ocr='ocr/profile')


class ProviderProfiles(QObject):
    changed = Signal()

    def __init__(self, settings, *, credential_store=None, locked=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.credentials = credential_store if credential_store is not None else KeychainCredentialStore()
        self.locked = locked or (lambda feature: False)
        self._message = ''
        self._profiles = {pid: dict(row) for pid, row in LOCAL_PROFILES.items()}
        try:
            rows = json.loads(settings.value('providers/profiles_v1', '[]', type=str))
            if not isinstance(rows, list):
                raise ValueError()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pid = row.get('id', '')
                if (not isinstance(pid, str) or not pid.startswith('user_') or len(pid) != 37
                        or any(c not in '0123456789abcdef' for c in pid[5:])):
                    continue
                try:
                    validated = self._validate(row.get('label', ''), row.get('api_base', ''), row.get('model', ''),
                                               row.get('auth_required', True))
                except (ValueError, TypeError):
                    self._message = lambda: QCoreApplication.translate('ProviderProfiles', '模型配置无法读取，请重新配置；已有任务未更改。')
                    continue
                self._profiles[pid] = dict(validated, id=pid, key_saved=bool(row.get('key_saved', False)))
        except (ValueError, TypeError):
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '模型配置无法读取，请重新配置；已有任务未更改。')
        # Retain a preview's user-supplied Ask route as metadata. No plaintext
        # key is opened automatically; the user can import it or enter it again.
        self.import_legacy_route()

    def import_legacy_route(self):
        settings = self.settings
        if not self.selected('ask') and settings.value('ask/endpoint', '', type=str):
            try:
                legacy_endpoint = normalize_base(settings.value('ask/endpoint', '', type=str))
                legacy = self._validate(QCoreApplication.translate('ProviderProfiles', '原问 AI 接口'), legacy_endpoint,
                                        settings.value('ask/model', '', type=str),
                                        not is_loopback(urlsplit(legacy_endpoint).hostname))
                pid = 'user_' + uuid.uuid4().hex
                self._profiles[pid] = dict(legacy, id=pid, key_saved=False)
                settings.setValue('ask/profile', pid)
                self._persist()
                self._message = lambda: QCoreApplication.translate('ProviderProfiles', '原问答接口已保留；请导入旧密钥或重新填写 API Key。')
                self.changed.emit()
            except ValueError:
                pass

    @staticmethod
    def _validate(label, endpoint, model, auth_required):
        label, model = str(label).strip(), str(model).strip()
        if not label or len(label) > 120 or not model or len(model) > 200:
            raise ValueError('Invalid name or model')
        if any(ord(c) < 32 for c in label + model):
            raise ValueError('Invalid name or model')
        return dict(label=label, api_base=normalize_base(endpoint), model=model,
                    auth_required=bool(auth_required))

    def _persist(self):
        self.settings.setValue('providers/profiles_v1', json.dumps(
            [p for pid, p in self._profiles.items() if not self.is_builtin(pid)], ensure_ascii=False))
        self.settings.sync()

    def contains(self, pid):
        return pid in self._profiles

    @staticmethod
    def is_builtin(pid):
        return pid in LOCAL_PROFILES

    def route(self, pid):
        if pid not in self._profiles:
            raise ValueError(QCoreApplication.translate('ProviderProfiles', '此任务的模型档案不存在，请恢复原档案或新建译本。'))
        row = dict(self._profiles[pid])
        row.update(local=is_loopback(urlsplit(row['api_base']).hostname), builtin=self.is_builtin(pid),
                   auth_provider='profile:' + pid,
                   file=row.get('file', ''), short=row['label'])
        return row

    def selected(self, feature):
        key = FEATURE_KEYS[feature]
        default = DEFAULT_LOCAL_PROFILE if feature in {'translation', 'review'} else ''
        return self.settings.value(key, default, type=str)

    def feature_route(self, feature):
        return self.route(self.selected(feature))

    def ready(self, pid):
        p = self._profiles.get(pid)
        return bool(p and (not p['auth_required'] or p['key_saved']))

    def key(self, pid):
        row = self.route(pid)
        key = self.credentials.get(pid) if row['key_saved'] else ''
        if row['auth_required'] and not key:
            raise CredentialError(QCoreApplication.translate('ProviderProfiles', '密钥不可用，请在模型设置中重新保存 API Key。'))
        return key

    @Property('QVariantList', notify=changed)
    def profiles(self):
        return [dict(self.route(pid), detail=p['model'],
                     display_label=p['label'] if self.is_builtin(pid) else
                         f"{p['label']} · {p['model']} · {urlsplit(p['api_base']).netloc}",
                     destination=urlsplit(p['api_base']).netloc) for pid, p in self._profiles.items()]

    @Property(str, notify=changed)
    def message(self):
        return self._message() if callable(self._message) else self._message

    @staticmethod
    def missing_label():
        return QCoreApplication.translate('ProviderProfiles', '模型档案缺失')

    @staticmethod
    def mismatched_route():
        return QCoreApplication.translate('ProviderProfiles', '任务的接口或模型与档案不一致，请恢复原档案或新建译本。')

    @Property(str, notify=changed)
    def reviewProfile(self):  # noqa: N802
        return self.selected('review')

    @Property(str, notify=changed)
    def askProfile(self):  # noqa: N802
        return self.selected('ask')

    @Property(str, notify=changed)
    def ocrProfile(self):  # noqa: N802
        return self.selected('ocr')

    @Slot(str, result='QVariantMap')
    def profile(self, pid):
        return self.route(pid) if self.contains(pid) else {}

    @Slot(str, str, result=bool)
    def select(self, feature, pid):
        if feature not in FEATURE_KEYS or not self.contains(pid) or self.locked(feature):
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '当前任务正在使用模型，请结束或取消后再切换。')
            self.changed.emit()
            return False
        self.settings.setValue(FEATURE_KEYS[feature], pid)
        self.settings.sync()
        self._message = lambda: QCoreApplication.translate('ProviderProfiles', '模型选择已保存，下一次请求生效。')
        self.changed.emit()
        return True

    @Slot(str, str, str, str, str, bool, result=str)
    def save(self, pid, label, endpoint, model, key, auth_required):
        """Changing a route creates another identity; old task routes survive."""
        try:
            row = self._validate(label, endpoint, model, auth_required)
            old = self._profiles.get(pid)
            if self.is_builtin(pid):
                raise ValueError('Preset cannot be edited')
            if old and any(self.selected(f) == pid and self.locked(f) for f in FEATURE_KEYS):
                raise ValueError('Profile is in use')
            same_route = old and all(old[k] == row[k] for k in ('api_base', 'model', 'auth_required'))
            if not same_route:
                pid = 'user_' + uuid.uuid4().hex
            if key:
                self.credentials.put(pid, str(key).strip())
                saved = True
            else:
                saved = bool(same_route and old['key_saved'])
                if not same_route and old and old['api_base'] == row['api_base'] and old['key_saved']:
                    existing = self.credentials.get(old['id'])
                    if existing:
                        self.credentials.put(pid, existing)
                        saved = True
            if row['auth_required'] and not saved:
                raise CredentialError('API Key required for this route')
            self._profiles[pid] = dict(row, id=pid, key_saved=saved)
            self._persist()
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '模型已保存。API Key 存于系统钥匙串；请选择使用它的功能。')
            self.changed.emit()
            return pid
        except (ValueError, CredentialError):
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '保存失败：检查 HTTPS 地址、模型名和 API Key；任务运行中不能编辑。')
            self.changed.emit()
            return ''

    def _consent_fingerprint(self, pid, feature):
        p = self.route(pid)
        return hashlib.sha256(json.dumps(dict(feature=feature, api_base=p['api_base'], model=p['model']),
                                        sort_keys=True).encode()).hexdigest()

    @Slot(str, str, result=bool)
    def consented(self, feature, pid):
        if feature not in FEATURE_KEYS or not self.contains(pid):
            return False
        # A user-configured loopback endpoint may forward to another machine.
        # Only immutable, built-in local HY presets have implicit permission.
        if self.is_builtin(pid) and feature != 'ocr':
            return True
        return self.settings.value(f'providers/consent/{pid}/{feature}', '', type=str) == self._consent_fingerprint(pid, feature)

    @Slot(str, str, bool, result=bool)
    def setConsent(self, feature, pid, allow):  # noqa: N802
        if feature not in FEATURE_KEYS or not self.contains(pid):
            return False
        self.settings.setValue(f'providers/consent/{pid}/{feature}',
                               self._consent_fingerprint(pid, feature) if allow else '')
        self.settings.sync()
        self.changed.emit()
        return True

    def require_consent(self, feature, pid):
        if not self.consented(feature, pid):
            raise ValueError(QCoreApplication.translate('ProviderProfiles', '请先在模型设置中确认此功能向所选服务发送材料。'))

    @Slot(str, result=bool)
    def forgetKey(self, pid):  # noqa: N802
        if self.is_builtin(pid) or not self.contains(pid):
            return False
        if any(self.selected(f) == pid and self.locked(f) for f in FEATURE_KEYS):
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '请先结束或取消使用此模型的任务，再清除密钥。')
            self.changed.emit()
            return False
        try:
            self.credentials.delete(pid)
            self._profiles[pid]['key_saved'] = False
            self._persist()
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '钥匙串中的此模型密钥已清除。任务与模型档案保留。')
            self.changed.emit()
            return True
        except CredentialError:
            self._message = lambda: QCoreApplication.translate('ProviderProfiles', '密钥未能清除，请检查系统钥匙串权限。')
            self.changed.emit()
            return False
