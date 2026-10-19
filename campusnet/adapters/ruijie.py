# -*- coding: utf-8 -*-
"""锐捷 ePortal 适配器（常见账号密码流程）。

支持范围：
- `/eportal/index.jsp` 页面；
- `POST /ePortal/InterFace.do?method=login`（userId/password/service/queryString）；
- 明文密码，或显式配置的 md5/base64/sha1。

如果页面使用 `passwordEncrypt=true`（JS RSA/AES 加密）或验证码，
适配器会在发送密码前停止，并给出明确提示。
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlsplit

from ..errors import (
    CaptchaRequiredError,
    CredentialError,
    LimitError,
    LoginError,
    NetworkError,
    PortalProtocolError,
    UnsupportedPortalError,
)
from ..forms import (
    detect_captcha_image,
    parse_forms,
)
from ..models import (
    Config,
    Detection,
    LoginResult,
    PageProbe,
    PortalContext,
)
from .base import ProtocolAdapter, Status


def _encode_password(password: str, encoding: str) -> str:
    if encoding == "md5":
        return hashlib.md5(password.encode("utf-8")).hexdigest()
    if encoding == "sha1":
        return hashlib.sha1(password.encode("utf-8")).hexdigest()
    if encoding == "base64":
        return base64.b64encode(password.encode("utf-8")).decode("ascii")
    return password


class RuijieAdapter(ProtocolAdapter):
    protocol = "ruijie"
    display_name = "锐捷 ePortal"

    @classmethod
    def detect(cls, page: PageProbe) -> Detection:
        url = (page.final_url or page.url or "").lower()
        html = (page.text or "").lower()
        reasons = []
        score = 0.0
        if "/eportal/" in url or "interfacedo" in url or "interface.do" in url:
            score = max(score, 0.95)
            reasons.append("URL 包含 /eportal/ 或 InterFace.do")
        if "eportal" in html:
            score = max(score, 0.85)
            reasons.append("页面包含 eportal")
        if "锐捷" in (page.text or "") or "ruijie" in html:
            score = max(score, 0.85)
            reasons.append("页面包含锐捷标识")
        if "userid" in html and "querystring" in html:
            score = max(score, 0.9)
            reasons.append("页面包含 ePortal 登录参数")
        return Detection(cls.protocol, cls.display_name, score, reasons)

    # -- 上下文 -------------------------------------------------------------

    @classmethod
    def _query_string(cls, cfg: Config, page: PageProbe, forms) -> str:
        if cfg.query_string:
            return cfg.query_string.strip().lstrip("?")

        for form in forms:
            for item in form.fields:
                if item.name.lower() in ("querystring", "query_string") and item.value:
                    return item.value.strip().lstrip("?")

        html = page.text or ""
        for pattern in (
            r"queryString\s*[:=]\s*[\"']([^\"']*)[\"']",
            r"queryString\s*=\s*window\.location\.search\.substring\(1\)",
        ):
            match = re.search(pattern, html, re.I)
            if match:
                if match.lastindex:
                    return match.group(1).strip().lstrip("?")
                break

        return urlsplit(page.final_url or page.url).query

    @classmethod
    def _service(cls, cfg: Config, page: PageProbe, forms) -> str:
        if cfg.service:
            return cfg.service
        for form in forms:
            for item in form.fields:
                if item.name.lower() == "service" and item.value:
                    return item.value
        html = page.text or ""
        match = re.search(
            r"(?:var\s+)?service\s*[:=]\s*[\"']([^\"']+)[\"']", html, re.I
        )
        if match:
            return match.group(1)
        match = re.search(
            r"<select[^>]+(?:id|name)=[\"']service[\"'][^>]*>(.*?)</select>",
            html,
            re.I | re.S,
        )
        if match:
            selected = re.search(
                r"<option[^>]*selected[^>]*value=[\"']([^\"']+)[\"']",
                match.group(1),
                re.I,
            )
            if selected:
                return selected.group(1)
            first = re.search(
                r"<option[^>]*value=[\"']([^\"']+)[\"']", match.group(1), re.I
            )
            if first:
                return first.group(1)
        return ""

    @classmethod
    def _encryption(cls, page: PageProbe) -> tuple[bool, list[str]]:
        html = page.text or ""
        notes = []
        match = re.search(
            r"passwordEncrypt\s*[:=]\s*[\"']?(true|false)", html, re.I
        )
        if match:
            required = match.group(1).lower() == "true"
            notes.append(f"页面 passwordEncrypt={match.group(1).lower()}")
            return required, notes

        low = html.lower()
        for marker in ("jsencrypt", "rsa", "aes", "strEnc".lower(), "encrypt("):
            if marker in low:
                notes.append(f"页面包含前端加密标记：{marker}")
                return True, notes
        return False, notes

    @classmethod
    def _captcha(cls, page: PageProbe, forms) -> bool:
        for form in forms:
            for item in form.fields:
                if item.disabled or item.type == "hidden":
                    continue
                if any(
                    token in item.identity
                    for token in ("validcode", "captcha", "verifycode", "验证码")
                ):
                    return True
        return detect_captcha_image(page.text or "")

    @classmethod
    def context_from_page(cls, cfg: Config, page: PageProbe) -> PortalContext:
        forms = parse_forms(page.text or "")
        encryption, encryption_notes = cls._encryption(page)
        extras = {
            "query_string": cls._query_string(cfg, page, forms),
            "service": cls._service(cfg, page, forms),
            "encryption_required": encryption,
            "encryption_notes": encryption_notes,
            "captcha": cls._captcha(page, forms),
            "forms": forms,
        }
        return PortalContext(
            protocol=cls.protocol,
            portal_url=page.final_url or page.url,
            base=page.origin(),
            referer=page.final_url or page.url,
            page=page,
            extras=extras,
            prepared=True,
        )

    def prepare(self, context: PortalContext) -> PortalContext:
        if context.prepared:
            return context
        url = context.portal_url or self.cfg.url
        if not url:
            raise UnsupportedPortalError("没有锐捷 ePortal 登录页地址")
        try:
            page = self.http.get_page(url, via="配置")
        except NetworkError as exc:
            raise NetworkError(f"无法打开锐捷登录页：{exc}") from exc
        return self.context_from_page(self.cfg, page)

    # -- 状态 ---------------------------------------------------------------

    def status(self, context: PortalContext) -> Status:
        return self.internet_status()

    # -- 登录 ---------------------------------------------------------------

    def _login_url(self, context: PortalContext) -> str:
        if self.cfg.login_url:
            return urljoin(context.portal_url, self.cfg.login_url)
        return context.base + "/ePortal/InterFace.do?method=login"

    def _wait_online(self, tries: int = 3, delay: float = 1.5) -> Optional[bool]:
        result: Optional[bool] = None
        for attempt in range(tries):
            result = self.http.check_internet(self.cfg.connectivity_urls)
            if result is True:
                return True
            if attempt < tries - 1:
                time.sleep(delay)
        return result

    @staticmethod
    def _raise_for_message(message: str):
        low = (message or "").lower()
        if any(token in low for token in ("验证码", "captcha", "validcode")):
            raise CaptchaRequiredError("ePortal 要求验证码，无法自动登录。")
        if any(
            token in low
            for token in (
                "密码错误",
                "用户名或密码",
                "账号或密码",
                "用户不存在",
                "用户名不存在",
                "认证失败",
                "incorrect password",
                "invalid password",
            )
        ):
            raise CredentialError(message or "账号或密码错误")
        if any(token in low for token in ("在线数", "已在线", "超过", "exceed")):
            raise LimitError(message or "在线设备数量可能已达上限")
        raise LoginError(message or "ePortal 拒绝了登录")

    def login(
        self, context: PortalContext, username: str, password: str
    ) -> LoginResult:
        context = self.prepare(context)
        extras = context.extras

        if extras.get("captcha"):
            raise CaptchaRequiredError(
                "该 ePortal 需要验证码，自动登录已停止（不会发送密码）。"
            )
        if extras.get("encryption_required") and (
            self.cfg.password_encoding or "auto"
        ).lower() == "auto":
            raise UnsupportedPortalError(
                "该 ePortal 使用前端加密密码（passwordEncrypt=true），"
                "当前版本无法自动处理。请先使用浏览器登录，或提交 issue。"
            )

        encoding = (self.cfg.password_encoding or "plain").lower()
        if encoding == "auto":
            encoding = "plain"
        if encoding not in ("plain", "md5", "sha1", "base64"):
            raise UnsupportedPortalError(f"不支持的密码编码：{encoding}")

        username_field = self.cfg.username_field or "userId"
        password_field = self.cfg.password_field or "password"
        data = {
            username_field: username,
            password_field: _encode_password(password, encoding),
            "service": extras.get("service", ""),
            "queryString": extras.get("query_string", ""),
            "operatorPwd": "",
            "operatorUserId": "",
            "validcode": "",
            "passwordEncrypt": "false",
        }
        data.update(self.cfg.extra_fields)

        headers = {
            "Referer": context.portal_url,
            "Origin": context.base,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        response = self.http.post(
            self._login_url(context), data=data, headers=headers
        )

        parsed: Optional[dict] = None
        try:
            parsed = self.http.parse_json_or_jsonp(response.text)
        except PortalProtocolError:
            parsed = None

        if parsed is not None:
            result = str(parsed.get("result", "")).lower()
            message = str(
                parsed.get("message")
                or parsed.get("msg")
                or parsed.get("error")
                or ""
            )
            if result == "success":
                return LoginResult(ok=True, message=message or "登录成功", raw=parsed)
            if result == "fail":
                self._raise_for_message(message)

        online = self._wait_online()
        if online is True:
            return LoginResult(ok=True, message="登录成功", raw=parsed)

        text = response.text or ""
        low = text.lower()
        if any(token in low for token in ("验证码", "captcha", "validcode")):
            raise CaptchaRequiredError("ePortal 要求验证码，无法自动登录。")
        if any(
            token in low
            for token in (
                "密码错误",
                "用户名或密码",
                "账号或密码",
                "用户不存在",
                "认证失败",
                "incorrect password",
                "invalid password",
            )
        ):
            raise CredentialError("ePortal 提示账号或密码错误。")
        raise LoginError(
            "ePortal 已返回响应，但仍然无法访问互联网。"
            "可能该学校使用了额外的加密/验证步骤。",
            retryable=False,
        )

    def verify(
        self, context: PortalContext, username: str, tries: int = 3
    ) -> Optional[dict]:
        if self._wait_online(tries=tries, delay=1.0) is True:
            return {"connectivity_only": True}
        return None
