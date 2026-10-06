"""Offline EPUB package reader: retain publisher XHTML/CSS/images, never run scripts."""
import hashlib
import io
import json
import os
import posixpath
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit
from lxml import etree
from PIL import Image
from storage_context import StorageContext
import ui_messages

REVISION = 'epub-reader-v6-storage-lease'
CACHE = StorageContext.production().cache_root
THUMBNAILS = StorageContext.production().thumbnail_root
CSP = "default-src 'none'; img-src file: data:; style-src file: 'unsafe-inline'; font-src file: data:; media-src 'none'; script-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"
_DIGEST_CACHE = OrderedDict()
_ACTIVE_CACHE_ROOTS: dict[str, int] = {}
_CACHE_LOCK = threading.RLock()
_CLEANUP_QUEUE = queue.Queue()
_CLEANUP_THREAD = None


def _cache_path(cache):
    return Path(CACHE if cache is None else cache).expanduser().resolve()


@contextmanager
def _cache_transaction(cache):
    """Serialize preparation and reclamation across threads and processes."""
    cache = _cache_path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / '.cache.lock'
    with _CACHE_LOCK:
        with lock_path.open('a+b') as stream:
            try:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                # The application currently ships on macOS.  The in-process lock
                # remains a safe fallback for unsupported development hosts.
                fcntl = None
            try:
                yield cache
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _lease_dir(root):
    return Path(root) / '.leases'


def _active_lease_files(root):
    leases = _lease_dir(root)
    if not leases.is_dir():
        return []
    active = []
    for path in leases.glob('*.json'):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if _pid_alive(payload.get('pid')):
                active.append(path)
            else:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            # A freshly created or temporarily unreadable lease is protected.
            active.append(path)
    if not active:
        try:
            leases.rmdir()
        except OSError:
            pass
    return active


class CacheLease:
    """An idempotent, cross-process hold on one immutable cache generation."""

    __slots__ = ('root', 'token', '_released')

    def __init__(self, root):
        self.root = str(Path(root).resolve())
        self.token = f'{os.getpid()}-{uuid.uuid4().hex}'
        self._released = False
        leases = _lease_dir(self.root)
        leases.mkdir(parents=True, exist_ok=True)
        path = leases / (self.token + '.json')
        payload = json.dumps({'pid': os.getpid(), 'created': time.time()})
        fd, temporary = tempfile.mkstemp(prefix='.lease-', dir=leases)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def release(self):
        if self._released:
            return
        self._released = True
        path = _lease_dir(self.root) / (self.token + '.json')
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            pass


def acquire_cache_lease(root):
    return CacheLease(root) if root else None


def source_digest(path):
    """Return a cached streaming digest for one unchanged source file."""
    path = Path(path).resolve()
    for attempt in range(2):
        before = path.stat()
        signature = (str(path), before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns, before.st_ctime_ns)
        with _CACHE_LOCK:
            cached = _DIGEST_CACHE.get(signature)
            if cached:
                _DIGEST_CACHE.move_to_end(signature)
                return cached
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                                        after.st_mtime_ns, after.st_ctime_ns):
            value = digest.hexdigest()
            with _CACHE_LOCK:
                _DIGEST_CACHE[signature] = value
                _DIGEST_CACHE.move_to_end(signature)
                while len(_DIGEST_CACHE) > 128:
                    _DIGEST_CACHE.popitem(last=False)
            return value
    raise OSError('EPUB 正在替换，请稍后重试')


def _book_cache_dir(cache, cache_identity):
    identity = str(cache_identity)
    return Path(cache) / ('book-' + hashlib.sha256(identity.encode()).hexdigest()[:24])


def _edition_cache_dir(path, cache, cache_identity=None, edition_identity=None):
    path = Path(path).resolve()
    identity = str(cache_identity or path)
    edition_key = str(edition_identity or path)
    edition = hashlib.sha256(edition_key.encode()).hexdigest()[:20]
    return _book_cache_dir(cache, identity) / ('edition-' + edition)


def retain_cache_root(root):
    if not root:
        return
    key = str(Path(root).resolve())
    with _CACHE_LOCK:
        _ACTIVE_CACHE_ROOTS[key] = _ACTIVE_CACHE_ROOTS.get(key, 0) + 1


def release_cache_root(root):
    if not root:
        return
    key = str(Path(root).resolve())
    with _CACHE_LOCK:
        count = _ACTIVE_CACHE_ROOTS.get(key, 0)
        if count <= 1:
            _ACTIVE_CACHE_ROOTS.pop(key, None)
        else:
            _ACTIVE_CACHE_ROOTS[key] = count - 1


def _root_is_protected(root, extra=()):
    key = str(Path(root).resolve())
    if key in _protected_cache_roots(extra):
        return True
    return bool(_active_lease_files(root))


def discard_cache_root(root):
    """Delete one superseded generation only when no live view retains it."""
    if not root:
        return
    candidate = Path(root).resolve()
    if (not candidate.name.startswith('generation-')
            or not candidate.parent.name.startswith('edition-')
            or not candidate.parent.parent.name.startswith('book-')):
        return
    if not candidate.is_dir():
        return
    cache = candidate.parents[2]
    with _cache_transaction(cache):
        if _root_is_protected(candidate):
            return
        shutil.rmtree(candidate, ignore_errors=True)
        for parent in (candidate.parent, candidate.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
        _gc_shared_assets(cache)


def _cleanup_worker():
    while True:
        action, args = _CLEANUP_QUEUE.get()
        try:
            action(*args)
        except Exception:
            # Cache reclamation is best-effort; the next bounded-cache pass or
            # explicit clear retries it. Reader state never depends on delete.
            pass
        finally:
            _CLEANUP_QUEUE.task_done()


def _schedule_cleanup(action, *args):
    global _CLEANUP_THREAD
    with _CACHE_LOCK:
        if _CLEANUP_THREAD is None or not _CLEANUP_THREAD.is_alive():
            _CLEANUP_THREAD = threading.Thread(
                target=_cleanup_worker, name='whale-cache-cleanup', daemon=True)
            _CLEANUP_THREAD.start()
    _CLEANUP_QUEUE.put((action, args))


def schedule_discard_cache_root(root):
    """Reclaim an obsolete generation without blocking the GUI thread."""
    if root:
        _schedule_cleanup(discard_cache_root, str(root))


def schedule_prune_book_cache(cache_identity, cache=None):
    """Reclaim a removed shelf book without blocking the GUI thread."""
    if cache_identity:
        _schedule_cleanup(prune_book_cache, str(cache_identity), cache)


def drain_cache_cleanup():
    """Wait for already-scheduled cleanup during orderly shutdown and tests."""
    _CLEANUP_QUEUE.join()


def _protected_cache_roots(extra=()):
    with _CACHE_LOCK:
        result = set(_ACTIVE_CACHE_ROOTS)
    result.update(str(Path(value).resolve()) for value in extra if value)
    return result


def _prune_edition(edition, *, keep=()):
    edition = Path(edition)
    if not edition.is_dir():
        return
    protected = _protected_cache_roots(keep)
    for candidate in edition.iterdir():
        if (not candidate.is_dir() or candidate.name.startswith('.import-')
                or not candidate.name.startswith('generation-')):
            continue
        if str(candidate.resolve()) not in protected and not _active_lease_files(candidate):
            shutil.rmtree(candidate, ignore_errors=True)


def prune_book_cache(cache_identity, cache=None):
    """Remove disposable generations for a shelf book, preserving live views."""
    cache = _cache_path(cache)
    with _cache_transaction(cache):
        book = _book_cache_dir(cache, cache_identity)
        if not book.is_dir():
            return
        protected = _protected_cache_roots()
        for generation in book.glob('edition-*/generation-*'):
            if (generation.is_dir() and str(generation.resolve()) not in protected
                    and not _active_lease_files(generation)):
                shutil.rmtree(generation, ignore_errors=True)
        for edition in book.glob('edition-*'):
            try:
                edition.rmdir()
            except OSError:
                pass
        try:
            book.rmdir()
        except OSError:
            pass
        _gc_shared_assets(cache)


def cache_stats(cache=None, *, known_identities=()):
    cache = _cache_path(cache)
    total = files = generations = books = 0
    physical_total = 0
    seen_inodes = set()
    active = unknown = managed = 0
    if not cache.is_dir():
        return dict(bytes=0, physical_bytes=0, files=0, generations=0, books=0,
                    active=0, managed=0, unknown=0, reclaimable=0)
    known_books = {_book_cache_dir(cache, value).name for value in known_identities if value}
    for book in cache.glob('book-*'):
        if book.is_dir():
            books += 1
    for path in cache.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir() and path.name.startswith('generation-'):
            generations += 1
            if _root_is_protected(path):
                active += 1
            if (path / 'cache-meta.json').is_file() or path.parents[1].name in known_books:
                managed += 1
            else:
                unknown += 1
        elif path.is_file():
            try:
                stat = path.stat()
                total += stat.st_size
                files += 1
                inode = (stat.st_dev, stat.st_ino)
                if inode not in seen_inodes:
                    seen_inodes.add(inode)
                    physical_total += stat.st_size
            except OSError:
                pass
    return dict(bytes=total, physical_bytes=physical_total, files=files,
                generations=generations, books=books, active=active,
                managed=managed, unknown=unknown,
                reclaimable=max(0, generations - active))


def clear_epub_cache(cache=None):
    """Clear every inactive cache generation without invalidating a live page."""
    cache = _cache_path(cache)
    before = cache_stats(cache)
    if not cache.is_dir():
        return {**before, 'freed': 0, 'remaining': 0}
    with _cache_transaction(cache):
        protected = _protected_cache_roots()
        for candidate in list(cache.iterdir()):
            if (candidate.is_dir() and re.fullmatch(r'[0-9a-f]{64}', candidate.name)
                    and str(candidate.resolve()) not in protected
                    and not _active_lease_files(candidate)):
                shutil.rmtree(candidate, ignore_errors=True)
        for book in cache.glob('book-*'):
            if not book.is_dir():
                continue
            for generation in book.glob('edition-*/generation-*'):
                if (generation.is_dir() and str(generation.resolve()) not in protected
                        and not _active_lease_files(generation)):
                    shutil.rmtree(generation, ignore_errors=True)
            for stage in book.glob('edition-*/.import-*'):
                if stage.is_dir() and str(stage.resolve()) not in protected:
                    shutil.rmtree(stage, ignore_errors=True)
            for edition in book.glob('edition-*'):
                try:
                    edition.rmdir()
                except OSError:
                    pass
            try:
                book.rmdir()
            except OSError:
                pass
        _gc_shared_assets(cache)
    after = cache_stats(cache)
    return {**after, 'freed': max(0, before['bytes'] - after['bytes']),
            'remaining': after['bytes'],
            'physical_freed': max(0, before['physical_bytes'] - after['physical_bytes'])}


def _archive_entries(archive):
    entries = archive.infolist()
    if (len(entries) > 10000
            or sum(entry.file_size for entry in entries) > 300 * 1024 * 1024
            or any(entry.file_size > 32 * 1024 * 1024 for entry in entries)):
        raise ValueError('EPUB 解压体积超出安全上限')
    if 'META-INF/encryption.xml' in archive.namelist():
        raise ValueError('此 EPUB 含加密或混淆资源，当前版本暂不支持；原文件未修改')
    names = set()
    for entry in entries:
        name = member('', entry.filename.rstrip('/'))
        if name in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('EPUB 含重复路径或符号链接')
        names.add(name)
    return entries, names


def _package_documents(archive):
    try:
        container = xml(archive.read('META-INF/container.xml'))
        opf_path = member('', next(
            node.get('full-path') for node in container.iter()
            if local(node) == 'rootfile' and node.get('full-path')))
        opf = xml(archive.read(opf_path))
    except (KeyError, StopIteration, etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
        raise ValueError('EPUB 包结构不完整') from exc
    base = posixpath.dirname(opf_path)
    manifest = {node.get('id'): node for node in opf.iter()
                if local(node) == 'item' and node.get('id')}
    return opf_path, opf, base, manifest


def _cover_member(opf, manifest, base):
    cover_id = next((node.get('content') for node in opf.iter()
                     if local(node) == 'meta' and node.get('name') == 'cover'), '')
    cover_item = next((node for node in manifest.values()
                       if 'cover-image' in node.get('properties', '').split()
                       or node.get('id') == cover_id), None)
    if cover_item is None or not cover_item.get('href'):
        return ''
    return member(base, cover_item.get('href'))


def _write_thumbnail(data, destination):
    destination = Path(destination)
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.cover-', suffix='.png',
                                     dir=destination.parent)
    os.close(fd)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError('封面像素超过缩略图安全上限')
            image.load()
            image.thumbnail((320, 420), Image.Resampling.LANCZOS)
            if image.mode not in {'RGB', 'RGBA'}:
                image = image.convert('RGBA')
            image.save(temporary, format='PNG', optimize=True)
        os.replace(temporary, destination)
        return destination
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inspect_epub(path, *, thumbnail_root=None):
    """Read package metadata and a bounded cover without expanding the book."""
    path = Path(path).resolve()
    if path.stat().st_size > 150 * 1024 * 1024:
        raise ValueError('EPUB 超过当前 150 MB 阅读上限')
    source_hash = source_digest(path)
    try:
        with zipfile.ZipFile(path) as archive:
            _, names = _archive_entries(archive)
            _, opf, base, manifest = _package_documents(archive)
            chapter_count = 0
            for itemref in opf.iter():
                if local(itemref) != 'itemref' or itemref.get('linear') == 'no':
                    continue
                item = manifest.get(itemref.get('idref'))
                if item is None or 'html' not in item.get('media-type', ''):
                    continue
                name = member(base, item.get('href'))
                if name not in names:
                    raise ValueError('EPUB 缺少书脊章节')
                chapter_count += 1
            if not chapter_count:
                raise ValueError('EPUB 没有可阅读章节')
            title = next((''.join(node.itertext()).strip() for node in opf.iter()
                          if local(node) == 'title' and ''.join(node.itertext()).strip()), path.stem)
            author = next((''.join(node.itertext()).strip() for node in opf.iter()
                           if local(node) == 'creator'), '')
            cover = ''
            cover_name = _cover_member(opf, manifest, base)
            if thumbnail_root is not None and cover_name and cover_name in names:
                data = archive.read(cover_name)
                identity = hashlib.sha256(data).hexdigest()
                thumbnail = _write_thumbnail(
                    data, Path(thumbnail_root).expanduser().resolve() / (identity + '.png'))
                cover = str(thumbnail) if thumbnail else ''
            return dict(title=title, author=author, cover=cover,
                        source_hash=source_hash, chapters=chapter_count)
    except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError) as exc:
        raise ValueError('EPUB 文件损坏或格式不受支持') from exc


def xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def local(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else ''


def member(base, href):
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or '\\' in href:
        raise ValueError('EPUB 包含外部或非法资源路径')
    result = posixpath.normpath(posixpath.join(base, unquote(parts.path)))
    if result.startswith(('/', '../')) or result in ('..', '.'):
        raise ValueError('EPUB 路径越界')
    return result


def sanitize(data):
    root = xml(data)
    for node in list(root.iter()):
        if local(node) in {'script', 'iframe', 'object', 'embed', 'base', 'form'} or (local(node) == 'meta' and node.get('http-equiv', '').lower() == 'refresh'):
            if node.getparent() is not None:
                node.getparent().remove(node)
            continue
        for attr in list(node.attrib):
            if etree.QName(attr).localname.lower().startswith('on'):
                del node.attrib[attr]
    head = next((n for n in root.iter() if local(n) == 'head'), None)
    if head is None:
        head = etree.Element('{http://www.w3.org/1999/xhtml}head')
        root.insert(0, head)
    meta = etree.Element('{http://www.w3.org/1999/xhtml}meta')
    meta.set('http-equiv', 'Content-Security-Policy')
    meta.set('content', CSP)
    head.insert(0, meta)
    return etree.tostring(root, encoding='utf-8', xml_declaration=True)


def web_page(book, chapter):
    """Root-level reading portal gives WKWebView access to sibling resources."""
    root = Path(book['root']).resolve()
    original = (root / chapter).resolve()
    if not original.is_relative_to(root):
        raise ValueError('阅读章节路径越界')
    recorded = (book.get('portals') or {}).get(chapter)
    portal = ((root / recorded).resolve() if recorded else
              root / ('whale-page-' + hashlib.sha256(chapter.encode()).hexdigest()[:20] + '.xhtml'))
    if not portal.is_relative_to(root):
        raise ValueError('阅读页面路径越界')
    if portal.exists():
        return portal
    document = xml(original.read_bytes())
    chapter_base = posixpath.dirname(str(chapter))
    original_uri = original.as_uri()
    def rebase(value):
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path:
            return value if parts.path else original_uri + value
        try:
            # EPUB member names were already normalized and every symlink was
            # rejected during extraction.  Resolving each DOM/CSS reference
            # through the filesystem made image-heavy books spend tens of
            # seconds in realpath(); POSIX package-path validation is both
            # stronger for an archive and dramatically cheaper.
            relative = member(chapter_base, parts.path)
        except ValueError:
            return 'about:blank'
        target = root / relative
        return target.as_uri() + ('?' + parts.query if parts.query else '') + ('#' + parts.fragment if parts.fragment else '')
    def css(value):
        return re.sub(r'url\(\s*[\"\']?([^\"\')]+)[\"\']?\s*\)', lambda m: 'url("' + rebase(m[1].strip()) + '")', value)
    for node in document.iter():
        for attr, value in list(node.attrib.items()):
            name = etree.QName(attr).localname
            if name in {'src', 'href', 'poster'}:
                node.set(attr, rebase(value))
            elif name == 'style':
                node.set(attr, css(value))
        if local(node) == 'style' and node.text:
            node.text = css(node.text)
    from task_config import atomic
    atomic(portal, etree.tostring(document, encoding='unicode'))
    return portal


_SHARED_ASSET_SUFFIXES = {
    '.avif', '.bmp', '.gif', '.jpeg', '.jpg', '.png', '.webp',
    '.otf', '.ttf', '.woff', '.woff2',
}


def _write_generation_file(cache, destination, data):
    """Hard-link immutable binary assets across original/translated editions."""
    destination = Path(destination)
    if destination.suffix.lower() not in _SHARED_ASSET_SUFFIXES:
        destination.write_bytes(data)
        return
    digest = hashlib.sha256(data).hexdigest()
    asset = Path(cache) / 'assets-v1' / digest[:2] / (digest + destination.suffix.lower())
    asset.parent.mkdir(parents=True, exist_ok=True)
    if not asset.is_file():
        fd, temporary = tempfile.mkstemp(prefix='.asset-', dir=asset.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary, 0o444)
            except OSError:
                pass
            try:
                os.replace(temporary, asset)
            except OSError:
                if not asset.is_file():
                    raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    try:
        os.link(asset, destination)
    except OSError:
        # Cross-device and filesystems without hard-link support remain correct,
        # only without physical deduplication.
        destination.write_bytes(data)


def _cache_meta(book, *, path, cache_identity, edition_identity, producer_scope):
    root = Path(book['root'])
    total = files = 0
    for candidate in root.rglob('*'):
        if candidate.is_file() and '.leases' not in candidate.parts:
            try:
                total += candidate.stat().st_size
                files += 1
            except OSError:
                pass
    return {
        'schema': 1,
        'logical_book_id': str(cache_identity or Path(path).resolve()),
        'edition_id': str(edition_identity or Path(path).resolve()),
        'source_path': str(Path(path).resolve()),
        'source_hash': book.get('source_hash', ''),
        'generation': Path(book['root']).name,
        'revision': REVISION,
        'producer_scope': str(producer_scope or 'production'),
        'bytes': total,
        'files': files,
        'created': time.time(),
        'last_access': time.time(),
    }


def _touch_cache_meta(root):
    path = Path(root) / 'cache-meta.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        value['last_access'] = time.time()
        from task_config import atomic
        atomic(path, json.dumps(value, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError):
        pass


def _gc_shared_assets(cache):
    assets = Path(cache) / 'assets-v1'
    if not assets.is_dir():
        return
    for path in assets.rglob('*'):
        if not path.is_file():
            continue
        try:
            if path.stat().st_nlink <= 1:
                path.unlink()
        except OSError:
            pass
    for directory in sorted((path for path in assets.rglob('*') if path.is_dir()),
                            key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        assets.rmdir()
    except OSError:
        pass


def _enforce_cache_budget_locked(cache, budget, *, keep=()):
    """Bound indexed generations; preserve legacy/unknown caches for migration."""
    if not budget:
        return
    rows = []
    total = 0
    for generation in Path(cache).glob('book-*/edition-*/generation-*'):
        metadata = generation / 'cache-meta.json'
        if not metadata.is_file():
            continue
        try:
            value = json.loads(metadata.read_text(encoding='utf-8'))
            size = max(0, int(value.get('bytes') or 0))
            accessed = float(value.get('last_access') or value.get('created') or 0)
        except (OSError, ValueError, TypeError):
            continue
        rows.append((accessed, generation, size))
        total += size
    protected = _protected_cache_roots(keep)
    for _, generation, size in sorted(rows):
        if total <= int(budget):
            break
        if str(generation.resolve()) in protected or _active_lease_files(generation):
            continue
        shutil.rmtree(generation, ignore_errors=True)
        total -= size
        for parent in (generation.parent, generation.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
    _gc_shared_assets(cache)


def open_epub(path, cache=None, *, cache_identity=None, edition_identity=None,
              acquire_lease=False, producer_scope='production', cache_budget=None):
    cache = _cache_path(cache)
    with _cache_transaction(cache):
        book = _open_epub_locked(
            path, cache, cache_identity=cache_identity,
            edition_identity=edition_identity, producer_scope=producer_scope)
        if acquire_lease:
            lease = acquire_cache_lease(book.get('root'))
            _enforce_cache_budget_locked(cache, cache_budget, keep=(book.get('root'),))
            return book, lease
        _enforce_cache_budget_locked(cache, cache_budget, keep=(book.get('root'),))
        return book


def _open_epub_locked(path, cache, *, cache_identity=None, edition_identity=None,
                      producer_scope='production'):
    path = Path(path).resolve()
    if path.stat().st_size > 150 * 1024 * 1024:
        raise ValueError('EPUB 超过当前 150 MB 阅读上限')
    # Opening a book never deletes pre-migration or source-unknown caches.
    # They remain visible in statistics until the user confirms a clear.
    source_hash = source_digest(path)
    digest = hashlib.sha256((REVISION + '\0' + source_hash).encode()).hexdigest()
    edition = _edition_cache_dir(path, cache, cache_identity, edition_identity)
    target = edition / ('generation-' + digest)
    if ((target / 'book.json').is_file()
            and (target / 'cache-meta.json').is_file()):
        book = json.loads((target / 'book.json').read_text())
        _touch_cache_meta(target)
        _prune_edition(edition, keep=(target,))
        return book
    edition.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if str(target.resolve()) in _protected_cache_roots():
            raise OSError('当前阅读缓存不完整，请重启鲸读后重试')
        shutil.rmtree(target)
    stage = Path(tempfile.mkdtemp(prefix='.import-', dir=edition))
    retain_cache_root(stage)
    try:
        with zipfile.ZipFile(path) as archive:
            entries, names = _archive_entries(archive)
            for entry in entries:
                name = member('', entry.filename.rstrip('/'))
                if entry.is_dir():
                    continue
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(entry)
                if destination.suffix.lower() in {'.xhtml', '.html', '.htm'}:
                    data = sanitize(data)
                _write_generation_file(cache, destination, data)
            opf_path, opf, base, manifest = _package_documents(archive)
            labels = {}
            toc = []
            for item in manifest.values():
                if 'nav' in item.get('properties', '').split():
                    nav_path = member(base, item.get('href'))
                    nav = xml(archive.read(nav_path))
                    for node in nav.iter():
                        if local(node) == 'a' and node.get('href'):
                            href = node.get('href')
                            try:
                                name = member(posixpath.dirname(nav_path), href)
                            except ValueError:
                                continue
                            title = ''.join(node.itertext()).strip()
                            labels.setdefault(name, title)
                            toc.append(dict(title=title, path=name, fragment=urlsplit(href).fragment))
                elif item.get('media-type') == 'application/x-dtbncx+xml':
                    ncx_path = member(base, item.get('href'))
                    for point in xml(archive.read(ncx_path)).iter():
                        if local(point) != 'navPoint':
                            continue
                        content = next((n for n in point if local(n) == 'content'), None)
                        label = next((n for n in point if local(n) == 'navLabel'), None)
                        if content is not None and label is not None:
                            href = content.get('src', '')
                            name = member(posixpath.dirname(ncx_path), href)
                            title = ''.join(label.itertext()).strip()
                            labels.setdefault(name, title)
                            toc.append(dict(title=title, path=name, fragment=urlsplit(href).fragment))
            chapters = []
            for itemref in opf.iter():
                if local(itemref) != 'itemref' or itemref.get('linear') == 'no':
                    continue
                item = manifest.get(itemref.get('idref'))
                if item is None or 'html' not in item.get('media-type', ''):
                    continue
                name = member(base, item.get('href'))
                if name not in names:
                    raise ValueError('EPUB 缺少书脊章节')
                doc = xml(archive.read(name))
                heading = next((''.join(n.itertext()).strip() for n in doc.iter() if local(n) in {'h1','h2','title'} and ''.join(n.itertext()).strip()), '')
                mapped = [n for n in doc.iter() if n.get('data-whale-source-text')]
                pending = sum(n.get('data-whale-pending') == 'true' for n in mapped)
                # New metadata stores a semantic state, and a generated default
                # chapter label is marked as such so the reader can localize it
                # without ever replacing a publisher title that merely looks
                # similar.
                status = ui_messages.epub_state_value(len(mapped), pending)
                publisher = labels.get(name) or heading
                fallback = '' if publisher else f'第 {len(chapters)+1} 节'
                chapters.append(dict(title=publisher or fallback, path=name,
                                     translationState=status,
                                     generatedTitle=not bool(publisher),
                                     generatedIndex=0 if publisher else len(chapters) + 1))
            if not chapters:
                raise ValueError('EPUB 没有可阅读章节')
            title = next((''.join(n.itertext()).strip() for n in opf.iter() if local(n) == 'title'), path.stem)
            author = next((''.join(n.itertext()).strip() for n in opf.iter() if local(n) == 'creator'), '')
            cover_name = _cover_member(opf, manifest, base)
            cover = str(target / cover_name) if cover_name else ''
            documents = sorted(name for name in names
                               if Path(name).suffix.lower() in {'.xhtml', '.html', '.htm'})
            book = dict(title=title, author=author, cover=cover, chapters=chapters, toc=toc,
                        root=str(target), digest=digest, source_hash=source_hash, revision=REVISION,
                        documents=documents,
                        fixed=any(local(n) == 'meta' and n.get('property') == 'rendition:layout' and n.text == 'pre-paginated' for n in opf.iter()))
        try:
            stage.rename(target)
        except OSError:
            # Another preparation of the same immutable generation won the
            # race. Its completed book.json is the authority.
            if not (target / 'book.json').is_file():
                raise
            book = json.loads((target / 'book.json').read_text())
        else:
            portals = {}
            for document in book.get('documents', []):
                portal = web_page(book, document)
                portals[document] = portal.name
            book['portals'] = portals
            (target / 'book.json').write_text(json.dumps(book, ensure_ascii=False))
            meta = _cache_meta(
                book, path=path, cache_identity=cache_identity,
                edition_identity=edition_identity, producer_scope=producer_scope)
            (target / 'cache-meta.json').write_text(
                json.dumps(meta, ensure_ascii=False, indent=2))
        _prune_edition(edition, keep=(target,))
        _gc_shared_assets(cache)
        return book
    finally:
        release_cache_root(stage)
        if stage.exists():
            shutil.rmtree(stage)


def initialize_webengine():
    # Keep the initialization entry point for callers; no Chromium is loaded.
    import os
    os.environ['QT_WEBVIEW_PLUGIN'] = 'native'
    from PySide6.QtWebView import QtWebView
    QtWebView.initialize()


def secure_profile(parent):
    # WKWebView reads only sanitized offline chapters. CSP blocks remote
    # resources; navigation is handled by the trusted reader script.
    return None
