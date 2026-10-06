"""Small public AppKit calls; no external command or private selector."""
from __future__ import annotations

import ctypes as C
from pathlib import Path
import re
import sys


class AppKitBridge:
    def __init__(self):
        self.appkit = C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.objc = C.CDLL('/usr/lib/libobjc.A.dylib')
        self.objc.objc_getClass.argtypes, self.objc.objc_getClass.restype = [C.c_char_p], C.c_void_p
        self.objc.sel_registerName.argtypes, self.objc.sel_registerName.restype = [C.c_char_p], C.c_void_p

    def cls(self, name):
        return self.objc.objc_getClass(name.encode('ascii'))

    def send(self, obj, selector, *args, result=C.c_void_p, types=None):
        types = types or [C.c_void_p] * len(args)
        call = C.CFUNCTYPE(result, C.c_void_p, C.c_void_p, *types)(
            C.cast(self.objc.objc_msgSend, C.c_void_p).value)
        return call(obj, self.objc.sel_registerName(selector.encode('ascii')), *args)

    def string(self, text):
        return self.send(self.cls('NSString'), 'stringWithUTF8String:', str(text).encode('utf-8'),
                         types=[C.c_char_p])

    def url(self, path):
        return self.send(self.cls('NSURL'), 'fileURLWithPath:', self.string(Path(path).absolute()))


def _store_panel(title, initial='', file_filter='', *, directory=False, save=False):
    """Select with AppKit so the OS, rather than Qt's fallback, grants access."""
    bridge = AppKitBridge()
    pool = bridge.send(bridge.cls('NSAutoreleasePool'), 'new')
    try:
        panel = bridge.send(bridge.cls('NSSavePanel' if save else 'NSOpenPanel'),
                            'savePanel' if save else 'openPanel')
        bridge.send(panel, 'setTitle:', bridge.string(title), result=None)
        bridge.send(panel, 'setCanCreateDirectories:', True, result=None, types=[C.c_bool])
        if not save:
            for selector, value in (('setCanChooseDirectories:', directory),
                                    ('setCanChooseFiles:', not directory),
                                    ('setAllowsMultipleSelection:', False)):
                bridge.send(panel, selector, value, result=None, types=[C.c_bool])
        extensions = sorted(set(re.findall(r'\*\.([A-Za-z0-9]+)', file_filter)))
        if extensions and not directory:
            allowed = bridge.send(bridge.cls('NSMutableArray'), 'array')
            for extension in extensions:
                bridge.send(allowed, 'addObject:', bridge.string(extension), result=None)
            bridge.send(panel, 'setAllowedFileTypes:', allowed, result=None)
        if initial:
            path = Path(initial)
            if path.is_absolute():
                parent = path if directory or path.is_dir() else path.parent
                bridge.send(panel, 'setDirectoryURL:', bridge.url(parent), result=None)
            if save:
                bridge.send(panel, 'setNameFieldStringValue:', bridge.string(path.name), result=None)
        if bridge.send(panel, 'runModal', result=C.c_long) != 1:
            return ''
        url = bridge.send(panel, 'URL')
        path = bridge.send(url, 'path') if url else None
        value = bridge.send(path, 'UTF8String', result=C.c_char_p) if path else None
        return value.decode('utf-8') if value else ''
    finally:
        bridge.send(pool, 'drain', result=None)


def _use_store_panel():
    if sys.platform != 'darwin':
        return False
    from file_access import is_sandboxed
    return is_sandboxed()


def choose_open_file(parent, title, initial='', file_filter=''):
    if _use_store_panel():
        return _store_panel(title, initial, file_filter), ''
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getOpenFileName(parent, title, initial, file_filter)


def choose_save_file(parent, title, initial='', file_filter=''):
    if _use_store_panel():
        return _store_panel(title, initial, file_filter, save=True), ''
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getSaveFileName(parent, title, initial, file_filter)


def choose_directory(parent, title, initial=''):
    if _use_store_panel():
        return _store_panel(title, initial, directory=True)
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getExistingDirectory(parent, title, initial)


def reveal_file(path):
    """Select the actual output in Finder using NSWorkspace."""
    bridge = AppKitBridge()
    pool = bridge.send(bridge.cls('NSAutoreleasePool'), 'new')
    try:
        urls = bridge.send(bridge.cls('NSArray'), 'arrayWithObject:', bridge.url(path))
        workspace = bridge.send(bridge.cls('NSWorkspace'), 'sharedWorkspace')
        bridge.send(workspace, 'activateFileViewerSelectingURLs:', urls, result=None)
    finally:
        bridge.send(pool, 'drain', result=None)


def extract_document_text(path):
    """AppKit imports RTF/Word without launching textutil outside the sandbox."""
    bridge = AppKitBridge()
    pool = bridge.send(bridge.cls('NSAutoreleasePool'), 'new')
    document = None
    try:
        error = C.c_void_p()
        allocated = bridge.send(bridge.cls('NSAttributedString'), 'alloc')
        options = bridge.send(bridge.cls('NSDictionary'), 'dictionary')
        document = bridge.send(allocated, 'initWithURL:options:documentAttributes:error:',
            bridge.url(path), options, None, C.byref(error),
            types=[C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)])
        if not document:
            raise ValueError('The document could not be imported. Save it as TXT, RTF or DOCX.')
        text = bridge.send(document, 'string')
        value = bridge.send(text, 'UTF8String', result=C.c_char_p)
        return value.decode('utf-8') if value else ''
    finally:
        if document:
            bridge.send(document, 'release', result=None)
        bridge.send(pool, 'drain', result=None)
