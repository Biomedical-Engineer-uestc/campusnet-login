# -*- coding: utf-8 -*-
"""统一异常类型。"""

from __future__ import annotations


class CampusNetError(Exception):
    """所有可预期错误的基类。"""


class ConfigError(CampusNetError):
    pass


class NetworkError(CampusNetError):
    """网络不可达、超时、HTTP 状态异常。"""


class PortalProtocolError(CampusNetError):
    """门户返回的内容无法按当前协议解析。"""


class LoginError(CampusNetError):
    """门户明确拒绝了登录，但不是密码错误。"""

    def __init__(self, message: str, ecode: str = "", retryable: bool = True):
        super().__init__(message)
        self.ecode = ecode
        self.retryable = retryable


class CredentialError(LoginError):
    """密码错误、账号不存在、密码错误次数过多等。

    这类错误绝不能自动重试，否则可能把账号锁掉。
    """


class LimitError(LoginError):
    """在线设备数超限等需要人工处理的情况。"""


class UnsupportedPortalError(CampusNetError):
    """识别到了门户，但当前版本无法自动处理（验证码、JS 加密、SSO 等）。"""


class CaptchaRequiredError(UnsupportedPortalError):
    pass


class CredentialStorageError(CampusNetError):
    """无法安全保存密码。"""
