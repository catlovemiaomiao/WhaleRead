"""Application credentials in macOS Keychain; no shell or plaintext fallback."""
from __future__ import annotations

import ctypes as C
import sys


class CredentialError(RuntimeError):
    """A deliberately redacted error: OS status only, never secret values."""


class MemoryCredentialStore:
    """Explicitly injected for isolated QA; never selected for production."""
    def __init__(self):
        self._items = {}

    def get(self, account):
        return self._items.get(account, '')

    def put(self, account, secret):
        if secret:
            self._items[account] = secret
        else:
            self.delete(account)

    def delete(self, account):
        self._items.pop(account, None)


class KeychainCredentialStore:
    SERVICE = 'local.sindy.jingdu.providers'
    NOT_FOUND = -25300
    DUPLICATE = -25299

    def __init__(self, service=None):
        if sys.platform != 'darwin':
            raise CredentialError('macOS Keychain is required')
        from application_identity import bundle_identifier
        self.service = service or (bundle_identifier() + '.providers')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        for name, args, result in (
            ('CFStringCreateWithCString', [C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            ('CFDictionaryCreateMutable', [C.c_void_p, C.c_long, C.c_void_p, C.c_void_p], C.c_void_p),
            ('CFDictionarySetValue', [C.c_void_p, C.c_void_p, C.c_void_p], None),
            ('CFDataCreate', [C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            ('CFDataGetLength', [C.c_void_p], C.c_long),
            ('CFDataGetBytePtr', [C.c_void_p], C.c_void_p),
            ('CFRelease', [C.c_void_p], None),
        ):
            fn = getattr(self.cf, name)
            fn.argtypes, fn.restype = args, result
        for name, args in (
            ('SecItemCopyMatching', [C.c_void_p, C.POINTER(C.c_void_p)]),
            ('SecItemAdd', [C.c_void_p, C.POINTER(C.c_void_p)]),
            ('SecItemUpdate', [C.c_void_p, C.c_void_p]),
            ('SecItemDelete', [C.c_void_p]),
        ):
            fn = getattr(self.sec, name)
            fn.argtypes, fn.restype = args, C.c_int32

    def _constant(self, name):
        return C.c_void_p.in_dll(self.sec, name).value

    def _dict(self):
        return self.cf.CFDictionaryCreateMutable(
            None, 0, C.addressof(C.c_byte.in_dll(self.cf, 'kCFTypeDictionaryKeyCallBacks')),
            C.addressof(C.c_byte.in_dll(self.cf, 'kCFTypeDictionaryValueCallBacks')))

    def _set(self, mapping, name, value):
        self.cf.CFDictionarySetValue(mapping, self._constant(name), value)

    def _string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)

    def _query(self, account):
        query = self._dict()
        self._set(query, 'kSecClass', self._constant('kSecClassGenericPassword'))
        for field, value in (('kSecAttrService', self.service), ('kSecAttrAccount', account)):
            ref = self._string(value)
            try:
                self._set(query, field, ref)
            finally:
                self.cf.CFRelease(ref)
        return query

    @staticmethod
    def _check(status):
        if status:
            raise CredentialError(f'Keychain operation failed (OSStatus {status})')

    def get(self, account):
        query = self._query(account)
        result = C.c_void_p()
        try:
            self._set(query, 'kSecReturnData', C.c_void_p.in_dll(self.cf, 'kCFBooleanTrue').value)
            self._set(query, 'kSecMatchLimit', self._constant('kSecMatchLimitOne'))
            status = self.sec.SecItemCopyMatching(query, C.byref(result))
            if status == self.NOT_FOUND:
                return ''
            self._check(status)
            return C.string_at(self.cf.CFDataGetBytePtr(result),
                               self.cf.CFDataGetLength(result)).decode('utf-8')
        finally:
            if result.value:
                self.cf.CFRelease(result)
            self.cf.CFRelease(query)

    def put(self, account, secret):
        if not isinstance(secret, str) or '\r' in secret or '\n' in secret or len(secret) > 8192:
            raise CredentialError('Invalid API credential')
        if not secret:
            self.delete(account)
            return
        query, attributes = self._query(account), self._dict()
        encoded = secret.encode('utf-8')
        data = self.cf.CFDataCreate(None, encoded, len(encoded))
        try:
            self._set(attributes, 'kSecValueData', data)
            status = self.sec.SecItemUpdate(query, attributes)
            if status == self.NOT_FOUND:
                self._set(query, 'kSecValueData', data)
                status = self.sec.SecItemAdd(query, None)
                if status == self.DUPLICATE:
                    status = self.sec.SecItemUpdate(query, attributes)
            self._check(status)
            if self.get(account) != secret:
                raise CredentialError('Keychain verification failed')
        finally:
            self.cf.CFRelease(data)
            self.cf.CFRelease(attributes)
            self.cf.CFRelease(query)

    def delete(self, account):
        query = self._query(account)
        try:
            status = self.sec.SecItemDelete(query)
            if status != self.NOT_FOUND:
                self._check(status)
        finally:
            self.cf.CFRelease(query)
