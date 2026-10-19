# -*- coding: utf-8 -*-
"""通用账号/密码网页表单适配器（含 Dr.COM、H3C、WebAuth 等简单页面）。

这是“尽力而为”的适配器：
- 只支持普通 HTML 表单提交；
- 验证码、短信/OTP、SSO、前端 RSA/AES 加密等会明确拒绝，不会发送密码；
- 一次运行只尝试一种密码编码，绝不穷举，避免把账号锁掉。
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
    FormSelection,
    collect_hidden_fields,
    detect_captcha_image,
    detect_password_encoding,
    parse_forms,
    select_login_form,
)
from ..models import (
    Config,
    Detection,
    LoginResult,
    PageProbe,
    PortalContext,
)
from .base import ProtocolAdapter, Status


def _same_origin(a: str, b: str) -> bool:
    pa = urlsplit(a)
    pb = urlsplit(b)
    return (
        pa.scheme.lower() == pb.scheme.lower()
        and pa.netloc.lower() == pb.netloc.lower()
    )


def _encode_password(password: str, encoding: str) -> str:
    if encoding == "md5":
        return hashlib.md5(password.encode("utf-8")).hexdigest()
    if encoding == "sha1":
        return hashlib.sha1(password.encode("utf-8")).hexdigest()
    if encoding == "base64":
        return base64.b64encode(password.encode("utf-8")).decode("ascii")
    return password


class GenericAdapter(ProtocolAdapter):
    protocol = "generic"
    display_name = "通用网页表单"

    # -- 检测 ---------------------------------------------------------------

    @classmethod
    def detect(cls, page: PageProbe) -> Detection:
        forms = parse_forms(page.text)
        selection = select_login_form(forms)
        if selection is None:
            return Detection(cls.protocol, cls.display_name, 0.0, [])
        reasons = [
            f"检测到登录表单（账号字段 {selection.username_field.name}，"
            f"密码字段 {selection.password_field.name}）"
        ]
        if selection.captcha_fields or detect_captcha_image(page.text):
            reasons.append("页面包含验证码")
        encoding, notes = detect_password_encoding(page.text)
        if encoding != "plain":
            reasons.append(f"密码编码：{encoding}")
        reasons.extend(notes)
        low = (page.text or "").lower()
        if "drcom" in low or "城市热点" in (page.text or ""):
            reasons.append("疑似 Dr.COM/城市热点")
        if "webauth" in low:
            reasons.append("疑似 WebAuth")
        score = min(0.68, 0.55 + selection.score * 0.15)
        return Detection(cls.protocol, cls.display_name, score, reasons)

    @classmethod
    def context_from_page(cls, cfg: Config, page: PageProbe) -> PortalContext:
        forms = parse_forms(page.text)
        selection = select_login_form(
            forms,
            username_field_override=cfg.username_field,
            password_field_override=cfg.password_field,
        )
        extras = {
            "forms": forms,
            "selection": selection,
            "encoding": detect_password_encoding(page.text)[0],
            "encoding_notes": detect_password_encoding(page.text)[1],
            "captcha": bool(
                (selection and selection.captcha_fields)
                or detect_captcha_image(page.text)
            ),
            "sso": False,
        }
        return PortalContext(
            protocol=cls.protocol,
            portal_url=page.final_url or page.url,
            base=page.origin(),
            referer=page.final_url or page.url,
            page=page,
            extras=extras,
            prepared=selection is not None,
        )

    # -- 准备 ---------------------------------------------------------------

    @staticmethod
    def _client_redirect(html: str) -> Optional[str]:
        patterns = (
            r"window\.location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']",
            r"location\.replace\(\s*[\"']([^\"']+)[\"']\s*\)",
            r"location\.href\s*=\s*[\"']([^\"']+)[\"']",
        )
        for pattern in patterns:
            match = re.search(pattern, html or "", re.I)
            if match:
                return match.group(1)
        return None

    def _load_selection(self, url: str) -> PortalContext:
        page = self.http.get_page(url, via="配置")
        final_url = page.final_url or url
        html = page.text or ""

        redirect = self._client_redirect(html)
        if redirect:
            target = urljoin(final_url, redirect)
            if target != final_url:
                try:
                    redirected = self.http.get_page(target, via="JS 跳转")
                    if redirected.status_code == 200:
                        page = redirected
                        final_url = redirected.final_url or target
                        html = redirected.text or ""
                except NetworkError:
                    pass

        forms = parse_forms(html)
        selection = select_login_form(
            forms,
            username_field_override=self.cfg.username_field,
            password_field_override=self.cfg.password_field,
        )
        if selection is None:
            raise UnsupportedPortalError(
                "没有在页面里找到账号/密码表单。它可能是纯 JavaScript 渲染、"
                "统一身份认证/SSO，或者需要先在浏览器里完成其它步骤。"
            )
        encoding, notes = detect_password_encoding(html)
        return PortalContext(
            protocol=self.protocol,
            portal_url=final_url,
            base=page.origin(),
            referer=final_url,
            page=page,
            extras={
                "forms": forms,
                "selection": selection,
                "encoding": encoding,
                "encoding_notes": notes,
                "captcha": bool(
                    selection.captcha_fields or detect_captcha_image(html)
                ),
                "sso": False,
            },
            prepared=True,
        )

    def prepare(self, context: PortalContext) -> PortalContext:
        if context.prepared and context.extras.get("selection") is not None:
            return context
        url = context.portal_url or self.cfg.url
        if not url:
            raise UnsupportedPortalError("没有门户登录页地址")
        return self._load_selection(url)

    # -- 状态 ---------------------------------------------------------------

    def status(self, context: PortalContext) -> Status:
        return self.internet_status()

    # -- 登录 ---------------------------------------------------------------

    def _wait_online(self, tries: int = 3, delay: float = 1.5) -> Optional[bool]:
        result: Optional[bool] = None
        for attempt in range(tries):
            result = self.http.check_internet(self.cfg.connectivity_urls)
            if result is True:
                return True
            if attempt < tries - 1:
                time.sleep(delay)
        return result

    def _classify_failure(self, response_text: str, final_url: str):
        text = response_text or ""
        low = text.lower()
        combined = low + " " + (final_url or "").lower()

        if any(token in combined for token in ("验证码", "captcha", "validcode")):
            raise CaptchaRequiredError("门户要求验证码，无法自动登录。")
        if any(
            token in combined
            for token in (
                "在线数",
                "在线设备",
                "设备数量",
                "超出允许",
                "exceed",
                "online_num",
            )
        ):
            raise LimitError("在线设备数量可能已达上限。")
        if any(
            token in combined
            for token in (
                "密码错误",
                "密码不正确",
                "用户名或密码",
                "账号或密码",
                "用户名不存在",
                "账号不存在",
                "用户不存在",
                "incorrect password",
                "invalid password",
                "authentication failed",
                "bad credentials",
            )
        ):
            raise CredentialError("门户提示账号或密码错误。")
        if any(
            token in combined
            for token in ("登录成功", "认证成功", "登陆成功", "注销", "logout")
        ):
            raise LoginError(
                "页面提示登录成功，但仍然无法访问互联网。"
                "该门户可能需要 JavaScript 或额外的认证步骤。",
                retryable=False,
            )
        raise LoginError(
            "提交表单后仍无法访问互联网。可能该门户不是简单的账号密码表单，"
            "或需要额外字段。可在 config.ini 里配置 extra_fields，"
            "或提交 issue 时附上 --diagnose 的输出。",
            retryable=False,
        )

    def login(
        self, context: PortalContext, username: str, password: str
    ) -> LoginResult:
        context = self.prepare(context)
        selection: FormSelection = context.extras["selection"]
        form = selection.form

        if context.extras.get("captcha"):
            raise CaptchaRequiredError(
                "该门户需要验证码，自动登录已停止（不会发送密码）。"
            )
        if selection.otp_fields:
            raise UnsupportedPortalError(
                "该门户需要短信/动态验证码，当前版本不支持自动登录。"
            )

        action = self.cfg.login_url or form.action or context.portal_url
        action = urljoin(context.portal_url, action)
        if not _same_origin(action, context.base) and not self.cfg.allow_cross_origin:
            raise UnsupportedPortalError(
                f"登录表单提交到不同域名：{action}。"
                "为安全起见默认拒绝发送密码；确认安全后可在 config.ini 设置 "
                "allow_cross_origin = true。"
            )

        encoding = (self.cfg.password_encoding or "auto").lower()
        if encoding == "auto":
            encoding = str(context.extras.get("encoding") or "plain")
        if encoding == "unsupported":
            raise UnsupportedPortalError(
                "检测到前端 RSA/AES 等密码加密，当前版本无法自动处理。"
                "（" + "；".join(context.extras.get("encoding_notes") or []) + "）"
            )
        if encoding not in ("plain", "md5", "sha1", "base64"):
            raise UnsupportedPortalError(f"不支持的密码编码：{encoding}")

        data = collect_hidden_fields(form)
        data.update(self.cfg.extra_fields)
        data[selection.username_field.name] = username
        data[selection.password_field.name] = _encode_password(password, encoding)

        headers = {
            "Referer": context.portal_url,
            "Origin": context.base,
            "X-Requested-With": "XMLHttpRequest",
        }

        if form.method.upper() == "GET":
            response = self.http.get(action, params=data, headers=headers)
        else:
            response = self.http.post(action, data=data, headers=headers)

        online = self._wait_online()
        if online is True:
            return LoginResult(
                ok=True,
                message="登录成功",
                raw={"final_url": response.final_url},
            )
        self._classify_failure(response.text, response.final_url)

    def verify(
        self, context: PortalContext, username: str, tries: int = 3
    ) -> Optional[dict]:
        if self._wait_online(tries=tries, delay=1.0) is True:
            return {"connectivity_only": True}
        return None
