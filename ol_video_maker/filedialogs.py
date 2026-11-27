"""Rutorna där man väljer filer.

I Windows används Windows egen ruta (GetOpenFileNameW i comdlg32, ingår i Windows) direkt
via ctypes. Den hamnar framför programfönstret, och man kan markera flera filer med Ctrl
eller Shift (GoPro delar ett långt lopp i flera filer: GS01…, GS02…).
På andra system används tkinters rutor.

filetypes har samma form som i tkinter: [("Videofiler", "*.mp4 *.mov"), ("Alla filer", "*.*")].
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Sequence, Tuple

FileTypes = Sequence[Tuple[str, str]]

OFN_READONLY_HIDE = 0x00000004
OFN_NOCHANGEDIR = 0x00000008
OFN_ALLOWMULTISELECT = 0x00000200
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
BUFFER_CHARS = 65536  # plats för många långa filnamn


def win_filter(filetypes: FileTypes) -> str:
    """Filtret som Windows vill ha: "Namn\\0*.mp4;*.mov\\0…\\0\\0"."""
    parts = []
    for name, patterns in filetypes:
        parts += [name, ";".join(patterns.split())]
    return "\0".join(parts) + "\0\0"


def parse_multi(text: str) -> List[str]:
    """Svaret från rutan. En fil: "C:\\a\\b.mp4". Flera: "C:\\a\\0b.mp4\\0c.mp4" (mappen först)."""
    items = [p for p in text.split("\0") if p]
    if len(items) <= 1:
        return items
    folder = items[0]
    return [os.path.join(folder, name) for name in items[1:]]


def _ofn_struct():
    """Strukturen OPENFILENAMEW från Windows (commdlg.h)."""
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                    ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                    ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                    ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                    ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                    ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                    ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                    ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
                    ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
                    ("FlagsEx", wintypes.DWORD)]
    return OPENFILENAMEW


def check() -> str:
    """Kontroll i bygget: strukturen har den storlek Windows väntar sig, och comdlg32 finns."""
    import ctypes
    size = ctypes.sizeof(_ofn_struct())
    want = 152 if ctypes.sizeof(ctypes.c_void_p) == 8 else 88
    if size != want:
        raise RuntimeError(f"OPENFILENAMEW är {size} byte, väntade {want}")
    ctypes.windll.comdlg32.GetOpenFileNameW  # finns funktionen?
    return f"Filväljare: comdlg32, OPENFILENAMEW {size} byte"


def _win_dialog(save: bool, title: str, filetypes: FileTypes, multiple: bool = False,
                initialdir: Optional[str] = None, initialfile: Optional[str] = None,
                defext: Optional[str] = None) -> List[str]:
    import ctypes
    from ctypes import wintypes

    OPENFILENAMEW = _ofn_struct()

    buf = ctypes.create_unicode_buffer(initialfile or "", BUFFER_CHARS)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    # Utforskarens delar i rutan behöver COM (gör inget om det redan är startat).
    ctypes.windll.ole32.CoInitialize(None)
    # Programfönstret (Edge) är fönstret man just klickade i. Rutan ägs av det och
    # hamnar därför ovanpå det.
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    ofn.hwndOwner = user32.GetForegroundWindow()
    flt = win_filter(filetypes)
    ofn.lpstrFilter = flt
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = BUFFER_CHARS
    ofn.lpstrInitialDir = initialdir or None
    ofn.lpstrTitle = title
    ofn.lpstrDefExt = defext
    ofn.Flags = OFN_EXPLORER | OFN_NOCHANGEDIR | OFN_READONLY_HIDE | OFN_PATHMUSTEXIST
    comdlg = ctypes.windll.comdlg32
    if save:
        ok = comdlg.GetSaveFileNameW(ctypes.byref(ofn))
    else:
        ofn.Flags |= OFN_FILEMUSTEXIST | (OFN_ALLOWMULTISELECT if multiple else 0)
        ok = comdlg.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return []  # avbrutet
    text = ctypes.wstring_at(ctypes.addressof(buf), BUFFER_CHARS)
    text = text.split("\0\0", 1)[0]
    return parse_multi(text) if multiple else [text.split("\0", 1)[0]]


def ask_open(get_tk: Callable, title: str, filetypes: FileTypes,
             multiple: bool = False) -> List[str]:
    """Välj en eller flera filer. Tom lista om man avbryter."""
    if os.name == "nt":
        return _win_dialog(False, title, filetypes, multiple=multiple)
    from tkinter import filedialog
    root = get_tk()
    if multiple:
        return list(root.tk.splitlist(filedialog.askopenfilenames(
            parent=root, title=title, filetypes=list(filetypes)) or ()))
    f = filedialog.askopenfilename(parent=root, title=title, filetypes=list(filetypes))
    return [f] if f else []


def ask_save(get_tk: Callable, title: str, filetypes: FileTypes, current: str,
             defext: str = ".mp4") -> Optional[str]:
    """Välj var en fil ska sparas. None om man avbryter."""
    folder, name = os.path.dirname(current) or None, os.path.basename(current) or None
    if os.name == "nt":
        res = _win_dialog(True, title, filetypes, initialdir=folder, initialfile=name,
                          defext=defext.lstrip("."))
        return res[0] if res else None
    from tkinter import filedialog
    f = filedialog.asksaveasfilename(parent=get_tk(), title=title, defaultextension=defext,
                                     filetypes=list(filetypes), initialdir=folder,
                                     initialfile=name)
    return f or None
