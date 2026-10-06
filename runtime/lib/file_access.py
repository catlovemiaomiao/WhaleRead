"""Persistent user-selected file grants using public CoreFoundation APIs."""
from __future__ import annotations

import base64
import ctypes as C
import json
import os
from pathlib import Path
import sys


class FileAccessError(PermissionError):
    pass


class MacBookmarks:
    CREATE_SCOPE = 1 << 11
    READ_ONLY = 1 << 12
    RESOLVE_SCOPE = 1 << 10
    WITHOUT_UI = 1 << 8
    WITHOUT_MOUNT = 1 << 9
    WITHOUT_IMPLICIT_START = 1 << 15

    def __init__(self):
        if sys.platform != 'darwin':
            raise FileAccessError('macOS bookmarks are required')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        for name, args, result in (
            ('CFURLCreateFromFileSystemRepresentation', [C.c_void_p, C.c_char_p, C.c_long, C.c_bool], C.c_void_p),
            ('CFURLCreateBookmarkData', [C.c_void_p, C.c_void_p, C.c_ulong, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)], C.c_void_p),
            ('CFURLCreateByResolvingBookmarkData', [C.c_void_p, C.c_void_p, C.c_ulong, C.c_void_p, C.c_void_p, C.POINTER(C.c_bool), C.POINTER(C.c_void_p)], C.c_void_p),
            ('CFURLCopyFileSystemPath', [C.c_void_p, C.c_long], C.c_void_p),
            ('CFStringGetCString', [C.c_void_p, C.c_void_p, C.c_long, C.c_uint32], C.c_bool),
            ('CFURLStartAccessingSecurityScopedResource', [C.c_void_p], C.c_bool),
            ('CFURLStopAccessingSecurityScopedResource', [C.c_void_p], None),
            ('CFDataCreate', [C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            ('CFDataGetLength', [C.c_void_p], C.c_long),
            ('CFDataGetBytePtr', [C.c_void_p], C.c_void_p),
            ('CFErrorGetCode', [C.c_void_p], C.c_long),
            ('CFRelease', [C.c_void_p], None),
        ):
            fn = getattr(self.cf, name)
            fn.argtypes, fn.restype = args, result

    def _error(self, error, operation):
        code = self.cf.CFErrorGetCode(error) if error.value else 0
        raise FileAccessError(f'File authorization {operation} failed (OS code {code})')

    def create(self, path, *, read_only=False):
        path = Path(path).expanduser().absolute()
        encoded = os.fsencode(path)
        url = self.cf.CFURLCreateFromFileSystemRepresentation(None, encoded, len(encoded), path.is_dir())
        error = C.c_void_p()
        data = None
        try:
            if not url:
                raise FileAccessError('Invalid file URL')
            data = self.cf.CFURLCreateBookmarkData(None, url,
                self.CREATE_SCOPE | (self.READ_ONLY if read_only else 0), None, None, C.byref(error))
            if not data:
                self._error(error, 'creation')
            return C.string_at(self.cf.CFDataGetBytePtr(data), self.cf.CFDataGetLength(data))
        finally:
            for ref in (data, url, error.value):
                if ref:
                    self.cf.CFRelease(ref)

    def transfer(self, lease):
        """Create an ephemeral cross-process bookmark from an acquired URL.

        App-scoped persistent bookmarks belong to the GUI's identity. An
        inherit-only helper must receive an implicit ephemeral scope instead.
        This capability stays in the anonymous pipe and is never persisted.
        """
        if not lease.url:
            raise FileAccessError('File authorization is no longer active')
        error = C.c_void_p()
        data = None
        try:
            data = self.cf.CFURLCreateBookmarkData(None, lease.url, 0, None, None, C.byref(error))
            if not data:
                self._error(error, 'transfer')
            return C.string_at(self.cf.CFDataGetBytePtr(data), self.cf.CFDataGetLength(data))
        finally:
            for ref in (data, error.value):
                if ref:
                    self.cf.CFRelease(ref)

    def resolve(self, bookmark, *, persistent=True):
        if not isinstance(bookmark, bytes) or len(bookmark) > 1024 * 1024:
            raise FileAccessError('Invalid file bookmark')
        data = self.cf.CFDataCreate(None, bookmark, len(bookmark))
        stale, error = C.c_bool(), C.c_void_p()
        url = None
        text = None
        try:
            url = self.cf.CFURLCreateByResolvingBookmarkData(None, data,
                self.WITHOUT_UI | self.WITHOUT_MOUNT |
                (self.RESOLVE_SCOPE if persistent else self.WITHOUT_IMPLICIT_START),
                None, None, C.byref(stale), C.byref(error))
            if not url:
                self._error(error, 'resolution')
            text = self.cf.CFURLCopyFileSystemPath(url, 0)
            buf = C.create_string_buffer(32768)
            if not text or not self.cf.CFStringGetCString(text, buf, len(buf), 0x08000100):
                raise FileAccessError('Invalid bookmark path')
            active = bool(self.cf.CFURLStartAccessingSecurityScopedResource(url))
            lease = BookmarkLease(self, url, Path(os.fsdecode(buf.value)), bool(stale.value), active)
            url = None  # Owned by the lease until close().
            return lease
        finally:
            for ref in (data, url, text, error.value):
                if ref:
                    self.cf.CFRelease(ref)


class BookmarkLease:
    def __init__(self, api, url, path, stale, active):
        self.api, self.url, self.path, self.stale, self.active = api, url, path, stale, active

    def close(self):
        if self.url:
            if self.active:
                self.api.cf.CFURLStopAccessingSecurityScopedResource(self.url)
            self.api.cf.CFRelease(self.url)
            self.url = None


def application_home():
    """NSHomeDirectory respects the app container, unlike a inherited HOME."""
    if sys.platform != 'darwin':
        return Path.home()
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    foundation = C.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
    foundation.NSHomeDirectory.argtypes = []
    foundation.NSHomeDirectory.restype = C.c_void_p
    cf.CFStringGetCString.argtypes = [C.c_void_p, C.c_void_p, C.c_long, C.c_uint32]
    cf.CFStringGetCString.restype = C.c_bool
    buffer = C.create_string_buffer(32768)
    if not cf.CFStringGetCString(foundation.NSHomeDirectory(), buffer, len(buffer), 0x08000100):
        raise FileAccessError('Application home is unavailable')
    return Path(os.fsdecode(buffer.value))


def is_sandboxed():
    if sys.platform != 'darwin':
        return False
    from credential_store import KeychainCredentialStore
    bridge = KeychainCredentialStore(service='local.sindy.jingdu.entitlement-probe')
    sec, cf = bridge.sec, bridge.cf
    sec.SecTaskCreateFromSelf.argtypes, sec.SecTaskCreateFromSelf.restype = [C.c_void_p], C.c_void_p
    sec.SecTaskCopyValueForEntitlement.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
    sec.SecTaskCopyValueForEntitlement.restype = C.c_void_p
    cf.CFBooleanGetValue.argtypes, cf.CFBooleanGetValue.restype = [C.c_void_p], C.c_bool
    cf.CFGetTypeID.argtypes, cf.CFGetTypeID.restype = [C.c_void_p], C.c_ulong
    cf.CFBooleanGetTypeID.argtypes, cf.CFBooleanGetTypeID.restype = [], C.c_ulong
    task, name = sec.SecTaskCreateFromSelf(None), bridge._string('com.apple.security.app-sandbox')
    value, error = None, C.c_void_p()
    try:
        value = sec.SecTaskCopyValueForEntitlement(task, name, C.byref(error)) if task else None
        return bool(value and cf.CFGetTypeID(value) == cf.CFBooleanGetTypeID() and cf.CFBooleanGetValue(value))
    finally:
        for ref in (value, error.value, name, task):
            if ref:
                cf.CFRelease(ref)


class FileAccessManager:
    KEY = 'storage/file_grants_v1'

    def __init__(self, settings, *, internal_roots=(), strict=None, api=None):
        self.settings = settings
        self.internal_roots = tuple(Path(p).expanduser().resolve() for p in internal_roots)
        self.strict = is_sandboxed() if strict is None else bool(strict)
        self.api = api if api is not None else MacBookmarks() if sys.platform == 'darwin' else None
        self._leases = {}
        try:
            rows = json.loads(settings.value(self.KEY, '{}', type=str))
            self._rows = {path: row for path, row in rows.items()
                          if isinstance(path, str) and Path(path).is_absolute()
                          and isinstance(row, dict) and isinstance(row.get('bookmark'), str)
                          and len(row['bookmark']) <= 1400000} if isinstance(rows, dict) else {}
        except (ValueError, TypeError):
            self._rows = {}

    def _internal(self, path):
        return any(path == root or path.is_relative_to(root) for root in self.internal_roots)

    def _persist(self):
        self.settings.setValue(self.KEY, json.dumps(self._rows, ensure_ascii=False))
        self.settings.sync()

    def selected(self, path, *, read_only=False):
        """Call only after a native dialog/drop gave this path to the app."""
        path = Path(path).expanduser().resolve()
        if self._internal(path):
            return path
        if not self.api:
            if self.strict:
                raise FileAccessError('File authorization is unavailable')
            return path
        try:
            data = self.api.create(path, read_only=read_only)
            self._rows[str(path)] = dict(bookmark=base64.b64encode(data).decode('ascii'),
                                        read_only=bool(read_only), directory=path.is_dir())
            self._persist()
            return self.ensure(path)
        except (FileAccessError, OSError):
            if self.strict:
                raise
            return path

    def ensure(self, path):
        path = Path(path).expanduser().resolve()
        if self._internal(path):
            return path
        matched = sorted((p for p in self._rows if path == Path(p)
                          or (self._rows[p].get('directory') and path.is_relative_to(Path(p)))),
                         key=len, reverse=True)
        for original in matched:
            try:
                if original not in self._leases:
                    data = base64.b64decode(self._rows[original]['bookmark'], validate=True)
                    lease = self.api.resolve(data)
                    if self.strict and not lease.active:
                        lease.close()
                        raise FileAccessError('Persistent file authorization was not granted')
                    self._leases[original] = lease
                    if lease.stale:
                        replacement = self.api.create(lease.path, read_only=self._rows[original].get('read_only', False))
                        self._rows[original]['bookmark'] = base64.b64encode(replacement).decode('ascii')
                        self._persist()
                lease = self._leases[original]
                return lease.path / path.relative_to(original)
            except (OSError, ValueError, KeyError):
                continue
        if self.strict:
            raise FileAccessError('Please select this file or its folder again to authorize access')
        return path

    def worker_grants(self):
        # Recreate a transferable scope for each already acquired URL. Stored
        # app-scoped bookmarks cannot be resolved by the helper's identity.
        return [base64.b64encode(self.api.transfer(lease)).decode('ascii')
                for lease in self._leases.values()]

    def close(self):
        for lease in self._leases.values():
            lease.close()
        self._leases.clear()


def worker_leases(grants):
    leases = []
    if not grants:
        return leases
    api = MacBookmarks()
    strict = is_sandboxed()
    try:
        for encoded in grants:
            lease = api.resolve(base64.b64decode(encoded, validate=True), persistent=False)
            leases.append(lease)
            if strict and not lease.active:
                raise FileAccessError('Worker file authorization was not granted')
        return leases
    except Exception:
        for lease in leases:
            lease.close()
        raise
