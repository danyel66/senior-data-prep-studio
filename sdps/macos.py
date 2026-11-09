"""Small macOS niceties, all optional and fail-safe (they do nothing on other platforms or on error)."""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import sys


def set_app_name(name: str) -> bool:
    """Make the menu bar show `name` instead of "Python".

    Python runs as Python.app, so macOS reads the menu title from that bundle's CFBundleName.
    We overwrite it in the in-memory Info dictionary *only* when it is a known mutable dictionary
    class; anything else is left alone, because mutating an immutable dictionary would abort.
    Must be called before QApplication is created. Returns True if the name was changed.
    """
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.object_getClassName.restype = ctypes.c_char_p
        objc.object_getClassName.argtypes = [ctypes.c_void_p]
        send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        vp = ctypes.c_void_p
        msg0 = ctypes.CFUNCTYPE(vp, vp, vp)(send)                     # [obj sel]
        msg_cstr = ctypes.CFUNCTYPE(vp, vp, vp, ctypes.c_char_p)(send)  # [cls sel:"utf8"]
        msg_set = ctypes.CFUNCTYPE(None, vp, vp, vp, vp)(send)          # [dict setObject:v forKey:k]
        cls, sel = objc.objc_getClass, objc.sel_registerName

        def nsstring(text: str):
            return msg_cstr(cls(b"NSString"), sel(b"stringWithUTF8String:"), text.encode())

        bundle = msg0(cls(b"NSBundle"), sel(b"mainBundle"))
        changed = False
        for getter in (b"localizedInfoDictionary", b"infoDictionary"):
            info = msg0(bundle, sel(getter)) if bundle else None
            if not info:
                continue
            klass = objc.object_getClassName(info) or b""
            if klass not in (b"__NSDictionaryM", b"NSMutableDictionary"):
                continue                                              # not provably mutable: leave it
            msg_set(info, sel(b"setObject:forKey:"), nsstring(name), nsstring("CFBundleName"))
            changed = True
        return changed
    except Exception:
        return False


def launched_from_app() -> bool:
    return os.environ.get("SDPS_LAUNCHED_FROM_APP") == "1"


def alert(title: str, message: str) -> None:
    """Native alert box (used when there is no terminal to print to)."""
    if sys.platform != "darwin":
        return
    def q(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        f'display alert "{q(title)}" message "{q(message)}" as critical'],
                       timeout=600, check=False, capture_output=True)
    except Exception:
        pass
