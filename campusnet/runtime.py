# -*- coding: utf-8 -*-
"""运行路径、跨平台单实例、停止标记和少量系统工具。"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path
from typing import Optional

APP_NAME = "CampusNet"
IS_WINDOWS = os.name == "nt"
MUTEX_NAME = "Local\\CampusNet-Watch"


def _data_home() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


DATA_DIR = _data_home()
CREDENTIAL_PATH = DATA_DIR / "credential.dat"
LOG_PATH = DATA_DIR / "netkeeper.log"
STOP_PATH = DATA_DIR / "stop.flag"
LOCK_PATH = DATA_DIR / "watch.lock"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_config_path(explicit: Optional[Path] = None) -> Path:
    """按以下顺序找配置：显式参数 > 环境变量 > 当前目录 > 项目目录 > 用户数据目录。"""
    if explicit:
        return Path(explicit).expanduser()

    env = os.environ.get("CAMPUSNET_CONFIG")
    if env:
        return Path(env).expanduser()

    for candidate in (Path.cwd() / "config.ini", PROJECT_ROOT / "config.ini"):
        if candidate.exists():
            return candidate

    # 从源码目录直接运行时，配置放在项目根目录，便于打包/移动。
    if (PROJECT_ROOT / "campus_net.py").exists():
        return PROJECT_ROOT / "config.ini"
    return DATA_DIR / "config.ini"


def safe_print(message: str = "") -> None:
    if sys.stdout is None:
        return
    try:
        print(message)
    except Exception:
        pass


def show_message(title: str, message: str, error: bool = True) -> None:
    if not IS_WINDOWS:
        return
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    except Exception:
        pass


def _kernel32():
    import ctypes.wintypes as wt

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    dll.CreateMutexW.restype = ctypes.c_void_p
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    dll.CloseHandle.restype = wt.BOOL
    return dll


def acquire_watch_mutex():
    """Windows 用命名互斥量；其他平台用 flock。返回 False 表示已有实例。"""
    if IS_WINDOWS:
        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return object()
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        return handle

    import fcntl

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    return handle


def watch_is_running() -> bool:
    if IS_WINDOWS:
        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return False
        running = ctypes.get_last_error() == 183
        kernel32.CloseHandle(handle)
        return running

    import fcntl

    if not LOCK_PATH.exists():
        return False
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return True
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
    return False


def consume_stop_request() -> bool:
    if not STOP_PATH.exists():
        return False
    try:
        STOP_PATH.unlink()
    except OSError:
        pass
    return True


def sleep_with_stop(seconds: int) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if consume_stop_request():
            return True
        time.sleep(min(1.0, max(0.0, end - time.time())))
    return consume_stop_request()


def request_stop() -> bool:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STOP_PATH.write_text("stop", encoding="ascii")
        return True
    except OSError:
        return False
