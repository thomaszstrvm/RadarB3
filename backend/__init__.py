"""Preload only the integrity-checked project SQLite DLL on Windows."""
import ctypes
import hashlib
import os
from pathlib import Path

_dll = Path(__file__).resolve().parents[1] / 'vendor' / 'sqlite' / 'sqlite3.dll'
if os.name == 'nt' and _dll.is_file():
    if hashlib.sha256(_dll.read_bytes()).hexdigest() != '4b59d3cded9dc1608544160f3b4b10ac974c6ac0d6ffc23064e5f4c8dbd05aea':
        raise RuntimeError('SQLite DLL integrity mismatch; reinstall verified dependency.')
    _sqlite_handle = ctypes.WinDLL(str(_dll))
