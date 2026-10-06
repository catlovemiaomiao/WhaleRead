"""Merge preview preferences with a verified, reversible settings snapshot."""
from __future__ import annotations

import json
from pathlib import Path
import uuid
from PySide6.QtCore import QSettings

ALLOWED_PREFIXES = ('library/', 'reader/', 'editions/', 'language/', 'appearance/')
ALLOWED_KEYS = {'last_source', 'last_job', 'last_read_file', 'speed_mode',
                'ui/locale', 'interface/locale', 'ask/answer_locale', 'ask/endpoint', 'ask/model',
                'review/export_copy'}
MERGED_LIST_PREFIXES = ('reader/bookmarks_v1/', 'reader/paragraph_notes_v1/')


def snapshot(settings, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('settings-' + uuid.uuid4().hex + '.ini')
    backup = QSettings(str(path), QSettings.IniFormat)
    for key in settings.allKeys():
        backup.setValue(key, settings.value(key))
    backup.sync()
    if backup.status() != QSettings.NoError:
        raise OSError('Could not save settings backup')
    restored = QSettings(str(path), QSettings.IniFormat)
    if restored.allKeys() != settings.allKeys() or any(restored.value(key) != settings.value(key)
                                                      for key in settings.allKeys()):
        raise OSError('Settings backup verification failed')
    return path


def restore(settings, path):
    backup = QSettings(str(path), QSettings.IniFormat)
    if backup.status() != QSettings.NoError:
        raise OSError('Could not read settings backup')
    settings.clear()
    for key in backup.allKeys():
        settings.setValue(key, backup.value(key))
    settings.sync()
    if settings.status() != QSettings.NoError:
        raise OSError('Could not restore settings')


def _rows(raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, list):
        raise ValueError('Invalid library list')
    return value


def import_settings(source, target, backup_directory):
    source = Path(source)
    fmt = QSettings.NativeFormat if source.suffix.lower() == '.plist' else QSettings.IniFormat
    previous = QSettings(str(source), fmt)
    keys = previous.allKeys()
    if previous.status() != QSettings.NoError or not keys:
        raise ValueError('Could not read preview settings')
    allowed = {k: previous.value(k) for k in keys if k in ALLOWED_KEYS or k.startswith(ALLOWED_PREFIXES)}
    if not allowed:
        raise ValueError('No preview library or reader settings found')
    # Parse shelf data before any mutation, then retain existing records by id.
    for key in ('library/books_v1', 'library/books_v2'):
        if key in allowed:
            incoming = _rows(allowed[key])
            current = _rows(target.value(key, '[]', type=str))
            identities = {r.get('id') for r in current if isinstance(r, dict)}
            for row in incoming:
                if isinstance(row, dict) and row.get('id') and row['id'] not in identities:
                    current.append(row)
                    identities.add(row['id'])
            allowed[key] = json.dumps(current, ensure_ascii=False)
    for key in list(allowed):
        if key.startswith(MERGED_LIST_PREFIXES):
            incoming = _rows(allowed[key])
            current = _rows(target.value(key, '[]', type=str))
            identities = {r.get('id') for r in current if isinstance(r, dict)}
            for row in incoming:
                if isinstance(row, dict) and row.get('id') and row['id'] not in identities:
                    current.append(row)
                    identities.add(row['id'])
            allowed[key] = json.dumps(current, ensure_ascii=False)
    backup = snapshot(target, backup_directory)
    imported = 0
    try:
        for key, value in allowed.items():
            if key.startswith(('library/books_', *MERGED_LIST_PREFIXES)) or not target.contains(key):
                target.setValue(key, value)
                imported += 1
        target.sync()
        if target.status() != QSettings.NoError:
            raise OSError('Settings import failed')
    except Exception:
        restore(target, backup)
        raise
    return dict(backup=str(backup), imported=imported)
