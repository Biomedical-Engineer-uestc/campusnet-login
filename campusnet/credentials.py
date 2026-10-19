# -*- coding: utf-8 -*-
"""密码持久化。

Windows: DPAPI 加密文件，只有当前 Windows 账户能解密。
其他平台: 使用 keyring（系统钥匙串）；没安装 keyring 时拒绝落盘，
由调用方改为每次手动输入。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .errors import CredentialStorageError
from .runtime import CREDENTIAL_PATH, IS_WINDOWS

_ENTROPY = b"CampusNet:v2"
_KEYRING_SERVICE = "CampusNet"


class CredentialStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else CREDENTIAL_PATH
        self._keyring = None
        if not IS_WINDOWS:
            try:
                import keyring  # type: ignore

                self._keyring = keyring
            except Exception:
                self._keyring = None

    @property
    def available(self) -> bool:
        if IS_WINDOWS:
            return True
        return self._keyring is not None

    @property
    def backend(self) -> str:
        if IS_WINDOWS:
            return "Windows DPAPI"
        if self._keyring is not None:
            return "系统钥匙串 (keyring)"
        return "不可用"

    # -- 对外接口 ------------------------------------------------------------

    def load(self, username: str) -> Optional[str]:
        if IS_WINDOWS:
            if not self.path.exists():
                return None
            try:
                from . import _dpapi

                raw = _dpapi.unprotect(self.path.read_bytes(), _ENTROPY)
                payload = json.loads(raw.decode("utf-8"))
                if payload.get("username") != username:
                    return None
                password = payload.get("password")
                if isinstance(password, str) and password:
                    return password
            except Exception:
                return None
            return None

        if self._keyring is None:
            return None
        try:
            return self._keyring.get_password(_KEYRING_SERVICE, username)
        except Exception:
            return None

    def save(self, username: str, password: str) -> None:
        if IS_WINDOWS:
            from . import _dpapi

            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {
                    "version": 2,
                    "username": username,
                    "password": password,
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            blob = _dpapi.protect(payload, _ENTROPY)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, self.path)
            try:
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(self.path), 0x02)
            except Exception:
                pass
            return

        if self._keyring is None:
            raise CredentialStorageError(
                "当前平台没有可用的安全存储。请安装 keyring："
                "python -m pip install keyring"
            )
        try:
            self._keyring.set_password(_KEYRING_SERVICE, username, password)
        except Exception as exc:
            raise CredentialStorageError(f"写入系统钥匙串失败：{exc}") from exc

    def delete(self, username: str = "") -> bool:
        if IS_WINDOWS:
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False
            except OSError:
                return False

        if self._keyring is None or not username:
            return False
        try:
            self._keyring.delete_password(_KEYRING_SERVICE, username)
            return True
        except Exception:
            return False
