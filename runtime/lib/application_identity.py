"""Use the actual signed bundle id for per-app IPC and Keychain isolation."""
import ctypes as C
import sys


def bundle_identifier():
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        return 'local.sindy.jingdu'
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFBundleGetMainBundle.argtypes, cf.CFBundleGetMainBundle.restype = [], C.c_void_p
    cf.CFBundleGetIdentifier.argtypes, cf.CFBundleGetIdentifier.restype = [C.c_void_p], C.c_void_p
    cf.CFStringGetCString.argtypes = [C.c_void_p, C.c_void_p, C.c_long, C.c_uint32]
    cf.CFStringGetCString.restype = C.c_bool
    bundle = cf.CFBundleGetMainBundle()
    name = cf.CFBundleGetIdentifier(bundle) if bundle else None
    buffer = C.create_string_buffer(1024)
    if not name or not cf.CFStringGetCString(name, buffer, len(buffer), 0x08000100):
        raise RuntimeError('Application bundle identity is unavailable')
    return buffer.value.decode('utf-8')


def application_settings():
    from PySide6.QtCore import QSettings
    # Separate signed apps must not discover the preview's preferences through
    # a fixed organization domain. Import is an explicit, recoverable action.
    settings = QSettings(QSettings.NativeFormat, QSettings.UserScope, bundle_identifier(), '')
    # macOS global defaults are not this application's saved preferences.
    # Inheriting them makes a clean install look like an existing reader and
    # also defeats the boundary between separately signed QA applications.
    settings.setFallbacksEnabled(False)
    return settings
