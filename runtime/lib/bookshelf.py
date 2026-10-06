"""Content-addressed local bookshelf. Removing an entry never deletes a book."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from book_identity import (SCHEMA as IDENTITY_SCHEMA, aggregate_sha256, book_id,
                           canonical_path, file_sha256, legacy_path_id,
                           reader_token, shelf_id, task_identity, valid_sha256)
from task_config import inside
from storage_context import StorageContext


class Bookshelf:
    KEY = 'library/books_v2'
    LEGACY_KEY = 'library/books_v1'
    REMOVED_KEY = 'library/removed_v2'
    LEGACY_REMOVED_KEY = 'library/removed_v1'
    PER_BOOK_KEYS = (
        'reading/',
        'reader/bilingual_book/',
        'reader/mode_book/',
        'reader/bookmarks_v1/',
        'reader/paragraph_notes_v1/',
    )

    def __init__(self, settings, storage_context=None, *, migrate=False):
        self.settings = settings
        self.storage_context = storage_context or StorageContext.production()
        self.storage_context.assert_test_safe()
        has_v2 = settings.contains(self.KEY)
        values = self._json_value(self.KEY if has_v2 else self.LEGACY_KEY, [])
        removed_key = self.REMOVED_KEY if settings.contains(self.REMOVED_KEY) else self.LEGACY_REMOVED_KEY
        self.removed = {str(value) for value in self._json_value(removed_key, [])}
        self.rows = []
        self._legacy_id_map = {}
        for value in values:
            if not self._valid_row(value):
                continue
            # Opening protected user files while the controller is being
            # constructed can leave macOS waiting for a permission dialog
            # before a window exists.  Startup therefore upgrades only stored
            # metadata.  New imports are hashed by ``remember``; legacy rows
            # are promoted explicitly after the UI exists or by the release
            # migration command.
            row, old_reader_identities = self._upgrade_row(value, allow_io=False)
            self._merge_row(row)
            for old_reader_identity in old_reader_identities:
                self._migrate_reader_settings(old_reader_identity, row['reader_identity'])
        if not has_v2:
            # Preserve v1 keys for rollback. The new app writes only the
            # content-addressed index after this one-time copy.
            self.save()
            self.settings.setValue(
                'library/content_identity_v2_migrated',
                all(valid_sha256(row.get('book_hash')) for row in self.rows))
            if self.settings.value('library/imported_existing_v1', False, type=bool):
                self.settings.setValue('library/imported_existing_v2', True)
            self.settings.sync()
        if migrate:
            self.promote_all()
        # Startup is intentionally metadata-only after the one-time identity
        # migration. Expensive EPUB inspection remains on the reader queue.
        try:
            from PySide6.QtCore import QUrl
            for row in self.rows:
                cover_path = str(row.get('cover_path') or '')
                row['cover'] = QUrl.fromLocalFile(cover_path).toString() if cover_path else ''
        except ImportError:
            pass

    def _json_value(self, key, fallback):
        try:
            value = json.loads(self.settings.value(key, json.dumps(fallback), type=str))
            return value if isinstance(value, type(fallback)) else fallback
        except (ValueError, TypeError):
            return fallback

    @staticmethod
    def _valid_row(row):
        return (isinstance(row, dict) and row.get('kind') in ('task', 'text')
                and all(isinstance(row.get(key), str) for key in ('id', 'source', 'title'))
                and bool(row.get('source')))

    @staticmethod
    def _config_identity(root):
        try:
            config = yaml.safe_load((root / '翻译任务.yaml').read_text(encoding='utf-8'))
            value = ((config or {}).get('source') or {}).get('identity') or {}
            return str(value.get('sha256') or '') if valid_sha256(value.get('sha256')) else ''
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            return ''

    @staticmethod
    def _marker_identity(root):
        try:
            value = json.loads((root / '.whaleread-identity.json').read_text(encoding='utf-8'))
            digest = value.get('book_sha256')
            task_id = value.get('task_id')
            return (str(digest) if value.get('schema') == 1 and valid_sha256(digest)
                    and valid_sha256(task_id) else '')
        except (OSError, ValueError, TypeError):
            return ''

    @staticmethod
    def _epub_identity(root):
        try:
            value = json.loads((root / '.epub-source.json').read_text()).get('sha256')
            return str(value) if valid_sha256(value) else ''
        except (OSError, ValueError, TypeError):
            return ''

    @classmethod
    def content_hash(cls, kind, path, source=''):
        """Resolve a full source hash without incorporating any path bytes."""
        target = Path(path).expanduser().resolve()
        if kind == 'task':
            recorded = [value for value in (cls._marker_identity(target),
                                             cls._config_identity(target),
                                             cls._epub_identity(target)) if value]
            if len(set(recorded)) == 1:
                return recorded[0]
            if len(set(recorded)) > 1:
                # Conflicting task metadata must not silently bind one book's
                # progress and notes to another book.
                return ''
            try:
                config = yaml.safe_load((target / '翻译任务.yaml').read_text(encoding='utf-8'))
                paths = [inside(target, raw) for raw in ((config or {}).get('source') or {}).get('files', [])]
                if paths and all(candidate.is_file() for candidate in paths):
                    return aggregate_sha256([file_sha256(candidate) for candidate in paths])
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                pass
            source_path = Path(source).expanduser().resolve() if source else None
            if source_path is not None and source_path.is_file():
                return file_sha256(source_path)
            return ''
        source_path = Path(source or target).expanduser().resolve()
        return file_sha256(source_path) if source_path.is_file() else ''

    @classmethod
    def key(cls, kind, path, source=''):
        digest = cls.content_hash(kind, path, source)
        return shelf_id(kind, digest, path if kind == 'task' else '') if digest else legacy_path_id(kind, path)

    @classmethod
    def logical_key(cls, kind, path, source=''):
        digest = cls.content_hash(kind, path, source)
        return book_id(digest) if digest else str(path or source)

    @staticmethod
    def _reader_identity(identity, digest, job, source):
        return book_id(digest) if digest else str(job or source)

    @staticmethod
    def _stored_task_id(identity, digest):
        prefix = f'task:{digest}:'
        candidate = str(identity or '')[len(prefix):] if str(identity or '').startswith(prefix) else ''
        return candidate if valid_sha256(candidate) else ''

    def _upgrade_row(self, value, *, allow_io=False):
        row = dict(value)
        raw_source = str(row.get('source') or '')
        raw_job = str(row.get('job') or '')
        old_identity = str(row.get('reader_identity') or raw_job or raw_source)
        kind = row['kind']
        source = canonical_path(raw_source)
        job = canonical_path(raw_job) if raw_job else ''
        retained_source = (canonical_path(row.get('retained_source'))
                           if row.get('retained_source') else '')
        digest = str(row.get('book_hash') or '')
        if not valid_sha256(digest) and allow_io:
            digest = self.content_hash(kind, job or source, source)
        if not valid_sha256(digest):
            digest = ''
        task_id = str(row.get('task_id') or '')
        if kind == 'task' and digest:
            if not valid_sha256(task_id):
                task_id = self._stored_task_id(row.get('id'), digest)
            if not valid_sha256(task_id) and allow_io:
                task_id = task_identity(job, digest)
            identity = f'task:{digest}:{task_id}' if valid_sha256(task_id) else str(row['id'])
        elif digest:
            task_id = ''
            identity = f'source:{digest}'
        else:
            task_id = ''
            identity = str(row['id'])
        legacy_ids = {str(item) for item in row.get('legacy_ids', []) if isinstance(item, str)}
        if row['id'] != identity:
            legacy_ids.add(str(row['id']))
        locations = {canonical_path(item) for item in row.get('locations', [])
                     if isinstance(item, str) and item}
        locations.add(source)
        edition_jobs = {canonical_path(item) for item in row.get('edition_jobs', [])
                        if isinstance(item, str) and item}
        if job:
            edition_jobs.add(job)
        reader_identity = self._reader_identity(identity, digest, job, source)
        old_reader_identities = {
            str(item) for item in row.get('legacy_reader_identities', [])
            if isinstance(item, str) and item
        }
        if old_identity and old_identity != reader_identity:
            old_reader_identities.add(old_identity)
        row.update(
            id=identity,
            source=source,
            job=job,
            retained_source=retained_source,
            book_hash=digest,
            task_id=task_id,
            identity_schema=IDENTITY_SCHEMA if digest else 'path-v1',
            reader_identity=reader_identity,
            legacy_ids=sorted(legacy_ids),
            legacy_reader_identities=sorted(old_reader_identities),
            locations=sorted(locations),
            edition_jobs=sorted(edition_jobs),
        )
        for legacy in legacy_ids:
            self._legacy_id_map[legacy] = identity
        return row, old_reader_identities

    def promote_all(self):
        """Hash legacy shelf rows on an explicit, non-startup migration path."""
        mapping = {}
        candidates = [dict(row) for row in self.rows
                      if (not valid_sha256(row.get('book_hash'))
                          or (row.get('kind') == 'task'
                              and not valid_sha256(row.get('task_id'))))]
        for row in candidates:
            old_id = str(row['id'])
            try:
                new_id = self.remember(
                    row['source'], job=row.get('job', ''),
                    reading_file=row.get('reading_file', ''),
                    progress=float(row.get('progress') or 0), enrich=False)
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                continue
            current = next((item for item in self.rows if item['id'] == new_id), None)
            if current is not None and valid_sha256(current.get('book_hash')):
                mapping[old_id] = new_id
        pending = any(not valid_sha256(row.get('book_hash'))
                      or (row.get('kind') == 'task'
                          and not valid_sha256(row.get('task_id')))
                      for row in self.rows)
        self.settings.setValue('library/content_identity_v2_migrated', not pending)
        self.save()
        self.settings.sync()
        return mapping

    def _preferred(self, left, right):
        saved_last_job = self.settings.value('last_job', '', type=str)
        last_job = canonical_path(saved_last_job) if saved_last_job else ''
        left_ready = bool(left.get('job') and (Path(left['job']) / '翻译任务.yaml').is_file())
        right_ready = bool(right.get('job') and (Path(right['job']) / '翻译任务.yaml').is_file())
        if left_ready != right_ready:
            return (left, right) if left_ready else (right, left)
        if right.get('job') == last_job and left.get('job') != last_job:
            return right, left
        if left.get('job') == last_job and right.get('job') != last_job:
            return left, right
        if right.get('kind') == 'task' and left.get('kind') != 'task':
            return right, left
        if left.get('kind') == 'task' and right.get('kind') != 'task':
            return left, right
        def modified(row):
            try:
                return Path(row.get('job') or row.get('source') or '').stat().st_mtime_ns
            except OSError:
                return 0
        return (right, left) if modified(right) >= modified(left) else (left, right)

    def _merge_row(self, incoming):
        existing = next((row for row in self.rows if row['id'] == incoming['id']), None)
        if existing is None:
            self.rows.append(incoming)
            return incoming
        winner, other = self._preferred(existing, incoming)
        merged = {**other, **winner}
        for key in ('legacy_ids', 'legacy_reader_identities', 'locations', 'edition_jobs'):
            merged[key] = sorted({str(value) for row in (existing, incoming)
                                  for value in row.get(key, []) if value})
        merged['progress'] = max(float(existing.get('progress') or 0),
                                 float(incoming.get('progress') or 0))
        index = self.rows.index(existing)
        self.rows[index] = merged
        for legacy in merged['legacy_ids']:
            self._legacy_id_map[legacy] = merged['id']
        return merged

    @staticmethod
    def _json_rows(value):
        try:
            rows = json.loads(str(value))
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        except (ValueError, TypeError):
            return []

    def _migrate_reader_settings(self, old_identity, new_identity):
        if not old_identity or not new_identity or old_identity == new_identity:
            return
        old_token, new_token = reader_token(old_identity), reader_token(new_identity)
        if old_token == new_token:
            return
        for prefix in self.PER_BOOK_KEYS:
            old_key, new_key = prefix + old_token, prefix + new_token
            if not self.settings.contains(old_key):
                continue
            old_value = self.settings.value(old_key)
            if prefix not in {'reader/bookmarks_v1/', 'reader/paragraph_notes_v1/'}:
                if not self.settings.contains(new_key):
                    self.settings.setValue(new_key, old_value)
                continue
            current = self._json_rows(self.settings.value(new_key, '[]', type=str))
            incoming = self._json_rows(old_value)
            if prefix == 'reader/paragraph_notes_v1/':
                for row in incoming:
                    row['document_id'] = new_token
            known = {str(row.get('id') or '') for row in current}
            current.extend(row for row in incoming if str(row.get('id') or '') not in known)
            self.settings.setValue(new_key, json.dumps(current, ensure_ascii=False))

    def resolve_id(self, identity):
        value = str(identity or '')
        if any(row['id'] == value for row in self.rows):
            return value
        return self._legacy_id_map.get(value, value)

    def task_candidates_for_hash(self, digest, *, preferred_job=''):
        """Return translation editions for one exact book hash in UI order.

        This is deliberately metadata-only: shelf rendering and explicit imports
        must not traverse protected task folders on the GUI thread. Callers that
        need a usable translation still validate the selected task/output before
        opening it.
        """
        if not valid_sha256(digest):
            return []
        candidates = [row for row in reversed(self.rows)
                      if row.get('kind') == 'task' and row.get('book_hash') == digest]

        def prefer(raw_job):
            if not raw_job:
                return
            wanted = canonical_path(raw_job)
            for index, row in enumerate(candidates):
                if row.get('job') and canonical_path(row['job']) == wanted:
                    candidates.insert(0, candidates.pop(index))
                    return

        # The actively managed edition wins over the last persisted edition;
        # otherwise rows are newest-first, matching the collapsed shelf card.
        prefer(self.settings.value('last_job', '', type=str))
        prefer(preferred_job)
        return candidates

    def preferred_task_for_hash(self, digest, *, preferred_job=''):
        candidates = self.task_candidates_for_hash(
            digest, preferred_job=preferred_job)
        return candidates[0] if candidates else None

    def upgrade_descriptor(self, value):
        """Map a persisted path-era reading tab onto its content identity."""
        if not isinstance(value, dict):
            return value
        row = dict(value)
        identity = self.resolve_id(row.get('id'))
        current = next((item for item in self.rows if item['id'] == identity), None)
        if current is None and row.get('kind') in ('task', 'text'):
            raw_path = row.get('job') or row.get('source') or ''
            # Descriptor restoration is part of startup and must remain
            # metadata-only.  The row is promoted when the user next opens it.
            identity = str(row.get('id') or legacy_path_id(row['kind'], raw_path))
            current = next((item for item in self.rows if item['id'] == identity), None)
        old_reader = str(row.get('reader_identity') or row.get('job') or row.get('source') or '')
        if current is not None:
            modes = {key: row[key] for key in ('column_id', 'manual_empty', 'epub_mode',
                                               'reading_mode', 'reading_file') if key in row}
            row = {**current, **modes}
        else:
            row['id'] = identity
            row['reader_identity'] = self._reader_identity(
                identity, str(row.get('book_hash') or ''), str(row.get('job') or ''),
                str(row.get('source') or ''))
        self._migrate_reader_settings(old_reader, str(row.get('reader_identity') or ''))
        return row

    def enrich(self, row, source):
        if source.suffix.lower() != '.epub' or not source.is_file():
            return
        try:
            from epub_reader import inspect_epub
            from PySide6.QtCore import QUrl
            stat = source.stat()
            signature = [stat.st_dev, stat.st_ino, stat.st_size,
                         stat.st_mtime_ns, stat.st_ctime_ns]
            cover_path = str(row.get('cover_path') or '')
            if (row.get('metadata_signature') == signature
                    and row.get('title') and (not cover_path or Path(cover_path).is_file())):
                row['cover'] = (QUrl.fromLocalFile(cover_path).toString()
                                if cover_path else '')
                return
            metadata = inspect_epub(
                source, thumbnail_root=self.storage_context.thumbnail_root)
            row.update(title=metadata['title'], author=metadata.get('author', ''),
                       cover_path=metadata.get('cover', ''),
                       cover=QUrl.fromLocalFile(metadata['cover']).toString() if metadata.get('cover') else '',
                       metadata_signature=signature)
        except (OSError, ValueError, KeyError):
            pass

    def save(self):
        self.settings.setValue(self.KEY, json.dumps(self.rows, ensure_ascii=False))
        self.settings.setValue(self.REMOVED_KEY, json.dumps(sorted(self.removed)))

    def remember(self, source, *, job='', reading_file='', progress=0, restore=False,
                 enrich=True, content_digest=''):
        source_path = Path(source).expanduser().resolve()
        source_value = canonical_path(source_path)
        job_value = canonical_path(job) if job else ''
        title = source_path.stem
        if job and source_path.parent == Path(job).resolve() / '原文':
            title = re.sub(r'-[0-9a-f]{10}$', '', title)
        kind = 'task' if job else 'text'
        if content_digest and not valid_sha256(content_digest):
            raise ValueError('书籍内容哈希无效')
        digest = (str(content_digest) if content_digest else
                  self.content_hash(kind, job_value or source_value, source_value))
        identity = (shelf_id(kind, digest, job_value if kind == 'task' else '')
                    if digest else legacy_path_id(kind, job_value or source_value))
        task_id = (self._stored_task_id(identity, digest)
                   if kind == 'task' and digest else '')
        retained_source = ''
        if kind == 'task' and job_value:
            try:
                config = yaml.safe_load(
                    (Path(job_value) / '翻译任务.yaml').read_text(encoding='utf-8'))
                files = ((config or {}).get('source') or {}).get('files') or []
                if files:
                    retained_source = canonical_path(inside(Path(job_value), files[0]))
            except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
                pass
        legacy = legacy_path_id(kind, job_value or source_value)
        if restore and (identity in self.removed or legacy in self.removed):
            return identity
        self.removed.discard(identity)
        self.removed.discard(legacy)
        incoming = {
            'id': identity,
            'kind': kind,
            'title': title,
            'source': source_value,
            'job': job_value,
            'retained_source': retained_source,
            'progress': progress,
            'book_hash': digest,
            'task_id': task_id,
            'identity_schema': IDENTITY_SCHEMA if digest else 'path-v1',
            'reader_identity': self._reader_identity(identity, digest, job_value, source_value),
            'legacy_ids': [legacy] if legacy != identity else [],
            'locations': [source_value],
            'edition_jobs': [job_value] if job_value else [],
        }
        # A freshly selected source has a prospective task path before its
        # directory and persistent task marker exist. Once preparation creates
        # the marker, replace that provisional edition id in place.
        provisional = [
            row for row in self.rows
            if (row.get('kind') == kind and row['id'] != identity
                and (not valid_sha256(row.get('book_hash'))
                     or row.get('book_hash') == digest)
                and ((kind == 'task' and row.get('job') == job_value)
                     or (kind == 'text' and source_value in {
                         str(row.get('source') or ''),
                         *(str(item) for item in row.get('locations', []) if item),
                     })))
        ]
        for previous in provisional:
            incoming = {**previous, **incoming}
            incoming['title'] = str(previous.get('title') or incoming['title'])
            for key in ('author', 'cover_path', 'cover', 'metadata_signature'):
                if previous.get(key):
                    incoming[key] = previous[key]
            incoming['progress'] = max(float(previous.get('progress') or 0),
                                       float(incoming.get('progress') or 0))
            for key in ('legacy_ids', 'legacy_reader_identities', 'locations',
                        'edition_jobs'):
                incoming[key] = sorted({
                    str(value) for row in (previous, incoming)
                    for value in row.get(key, []) if value
                })
            incoming['legacy_ids'] = sorted({*incoming['legacy_ids'], previous['id']})
            self.rows.remove(previous)
            self._legacy_id_map[previous['id']] = identity
            self._migrate_reader_settings(
                str(previous.get('reader_identity') or previous.get('job')
                    or previous.get('source') or ''),
                incoming['reader_identity'])
        existing = next((item for item in self.rows if item['id'] == identity), None)
        if existing is not None:
            incoming = {**existing, **incoming}
            incoming['title'] = str(existing.get('title') or incoming['title'])
            for key in ('author', 'cover_path', 'cover', 'metadata_signature'):
                if existing.get(key):
                    incoming[key] = existing[key]
        row = self._merge_row(incoming)
        self._legacy_id_map[legacy] = identity
        self._migrate_reader_settings(job_value or source_value, row['reader_identity'])
        if enrich:
            self.enrich(row, source_path)
        if reading_file:
            row['reading_file'] = canonical_path(reading_file)
        self.save()
        pending = any(not valid_sha256(item.get('book_hash'))
                      or (item.get('kind') == 'task'
                          and not valid_sha256(item.get('task_id')))
                      for item in self.rows)
        self.settings.setValue('library/content_identity_v2_migrated', not pending)
        return identity

    def remove(self, identity):
        identity = self.resolve_id(identity)
        self.removed.add(identity)
        self.rows = [row for row in self.rows if row['id'] != identity]
        self.save()

    def bootstrap(self, workspace):
        # A migrated v1 library already encoded the user's removed choices;
        # do not re-add every historical task merely because the key changed.
        if self.settings.value('library/imported_existing_v2', False, type=bool):
            return
        for config_path in sorted(Path(workspace).glob('*/翻译任务.yaml')):
            try:
                root = config_path.parent.resolve()
                config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
                source = inside(root, config['source']['files'][0])
                if not source.is_file():
                    continue
                progress_path = root / '翻译进度.json'
                progress = json.loads(progress_path.read_text()) if progress_path.is_file() else {}
                total = int(progress.get('total_source_chars') or 0)
                fraction = min(1, int(progress.get('completed_source_chars') or 0) / total) if total else 0
                self.remember(source, job=str(root), progress=fraction, restore=True,
                              enrich=False)
            except (OSError, ValueError, TypeError, KeyError, IndexError, yaml.YAMLError):
                continue
        self.settings.setValue('library/imported_existing_v2', True)
