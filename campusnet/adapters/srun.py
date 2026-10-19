# -*- coding: utf-8 -*-
"""深澜 Srun 适配器。

加密细节与门户 Portal.js 保持一致，并用其 JS 参考实现做过向量校验。
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import socket
import struct
import time
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from ..errors import (
    CredentialError,
    LimitError,
    LoginError,
    NetworkError,
    PortalProtocolError,
)
from ..models import (
    Config,
    Detection,
    LoginResult,
    PageProbe,
    PortalContext,
)
from .base import ProtocolAdapter, Status

_SRUN_ALPHABET = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
_STD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


# ---------------------------------------------------------------------------
# Srun 加密
# ---------------------------------------------------------------------------


def _to_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le")
    if not raw:
        return []
    return list(struct.unpack("<%dH" % (len(raw) // 2), raw))


def _to_words(text: str, include_length: bool) -> list[int]:
    units = _to_units(text)
    words: list[int] = []
    for i in range(0, len(units), 4):
        chunk = units[i : i + 4] + [0] * 4
        words.append(
            (chunk[0] | chunk[1] << 8 | chunk[2] << 16 | chunk[3] << 24)
            & 0xFFFFFFFF
        )
    if include_length:
        words.append(len(units))
    return words


def _from_words(words: list[int]) -> bytes:
    out = bytearray()
    for word in words:
        out.extend(
            (
                word & 0xFF,
                (word >> 8) & 0xFF,
                (word >> 16) & 0xFF,
                (word >> 24) & 0xFF,
            )
        )
    return bytes(out)


def _xencode(payload: str, key: str) -> bytes:
    if payload == "":
        return b""
    data = _to_words(payload, True)
    key_words = _to_words(key, False)
    while len(key_words) < 4:
        key_words.append(0)

    n = len(data) - 1
    if n < 1:
        return _from_words(data)

    z = data[n]
    y = data[0]
    d = 0
    mask = 0xFFFFFFFF
    delta = 0x86014019 | 0x183639A0
    rounds = math.floor(6 + 52 / (n + 1))

    while rounds > 0:
        rounds -= 1
        d = (d + delta) & mask
        e = (d >> 2) & 3

        p = 0
        while p < n:
            y = data[p + 1]
            m = ((z >> 5) ^ ((y << 2) & mask)) & mask
            m = (m + (((y >> 3) ^ ((z << 4) & mask) ^ (d ^ y)) & mask)) & mask
            m = (m + ((key_words[(p & 3) ^ e] ^ z) & mask)) & mask
            z = data[p] = (data[p] + m) & mask
            p += 1

        y = data[0]
        m = ((z >> 5) ^ ((y << 2) & mask)) & mask
        m = (m + (((y >> 3) ^ ((z << 4) & mask) ^ (d ^ y)) & mask)) & mask
        m = (m + ((key_words[(p & 3) ^ e] ^ z) & mask)) & mask
        z = data[n] = (data[n] + m) & mask

    return _from_words(data)


def _srun_b64(raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return "".join(
        "=" if ch == "=" else _SRUN_ALPHABET[_STD_ALPHABET.index(ch)]
        for ch in encoded
    )


def build_auth_payload(
    username: str,
    password: str,
    ip: str,
    ac_id: str,
    token: str,
    n: int = 200,
    auth_type: int = 1,
) -> tuple[str, str, str]:
    hmd5 = hashlib.md5((password + token).encode("utf-8")).hexdigest()
    raw_info = json.dumps(
        {
            "username": username,
            "password": password,
            "ip": ip,
            "acid": ac_id,
            "enc_ver": "srun_bx1",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    info = "{SRBX1}" + _srun_b64(_xencode(raw_info, token))
    check_source = (
        f"{token}{username}{token}{hmd5}{token}{ac_id}{token}{ip}"
        f"{token}{n}{token}{auth_type}{token}{info}"
    )
    chksum = hashlib.sha1(check_source.encode("utf-8")).hexdigest()
    return hmd5, info, chksum


# ---------------------------------------------------------------------------
# 页面工具
# ---------------------------------------------------------------------------


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}"


def _query_value(url: str, key: str) -> Optional[str]:
    try:
        values = parse_qs(urlsplit(url).query).get(key)
    except Exception:
        return None
    return values[0] if values else None


def _config_value(html: str, key: str) -> Optional[str]:
    match = re.search(r"var\s+CONFIG\s*=\s*\{(.*?)\};", html, re.S)
    block = match.group(1) if match else html
    found = re.search(
        r"\b" + re.escape(key) + r"\s*:\s*[\"']([^\"']+)[\"']", block
    )
    return found.group(1).strip() if found else None


def _local_ip_for(base: str) -> Optional[str]:
    parts = urlsplit(base)
    host = parts.hostname
    if not host:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        family, socktype, proto, _, addr = infos[0]
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(3)
            sock.connect(addr)
            return sock.getsockname()[0]
    except OSError:
        return None


class SrunAdapter(ProtocolAdapter):
    protocol = "srun"
    display_name = "深澜 Srun"

    @classmethod
    def detect(cls, page: PageProbe) -> Detection:
        url = (page.final_url or page.url or "").lower()
        html = (page.text or "").lower()
        reasons = []
        score = 0.0
        if "srun_portal" in url:
            score = max(score, 0.95)
            reasons.append("URL 包含 srun_portal")
        if "/cgi-bin/get_challenge" in url or "/cgi-bin/srun_portal" in url:
            score = max(score, 0.95)
            reasons.append("URL 指向 Srun CGI 接口")
        if "srunflag" in page.headers:
            score = max(score, 0.9)
            reasons.append("响应头 SRunFlag")
        if "srun" in html and ("acid" in html or "get_challenge" in html):
            score = max(score, 0.85)
            reasons.append("页面包含 Srun 配置")
        return Detection(cls.protocol, cls.display_name, score, reasons)

    @classmethod
    def context_from_page(cls, cfg: Config, page: PageProbe) -> PortalContext:
        html = page.text or ""
        final_url = page.final_url or page.url
        ip = (
            _config_value(html, "ip")
            or _query_value(final_url, "wlanuserip")
            or _query_value(final_url, "userip")
            or _query_value(final_url, "ip")
        )
        if ip and not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
            ip = None
        ac_id = (
            _config_value(html, "acid")
            or _query_value(final_url, "ac_id")
            or cfg.ac_id
            or "1"
        )
        return PortalContext(
            protocol=cls.protocol,
            portal_url=final_url,
            base=_origin(final_url),
            ip=ip,
            ac_id=str(ac_id),
            referer=final_url,
            page=page,
            prepared=bool(ip),
        )

    # -- 准备 ---------------------------------------------------------------

    def _candidate_bases(self, context: Optional[PortalContext]) -> list[str]:
        candidates = []
        if context and context.base:
            candidates.append(context.base.rstrip("/"))
        if self.cfg.url and self.cfg.url.lower() != "auto":
            candidates.append(_origin(self.cfg.url))
        for host in self.cfg.hosts:
            candidates.append(host.rstrip("/"))
        deduped = []
        for base in candidates:
            if base and base not in deduped:
                deduped.append(base)
        return deduped

    def prepare(self, context: PortalContext) -> PortalContext:
        if context.prepared and context.ip and context.ac_id:
            return context

        last_error: Optional[Exception] = None
        for base in self._candidate_bases(context):
            try:
                url = base + "/srun_portal_pc"
                response = self.http.get(
                    url,
                    params={
                        "ac_id": context.ac_id or self.cfg.ac_id or "1",
                        "theme": self.cfg.theme or "pro",
                    },
                    headers={"Referer": context.referer or base + "/"},
                )
                if response.status_code != 200:
                    raise NetworkError(
                        f"门户登录页返回 HTTP {response.status_code}"
                    )
                final_url = response.final_url or url
                html = response.text or ""

                # 有些网关用 meta refresh 而不是 302 跳转。
                meta = re.search(
                    r"http-equiv=[\"']?refresh[\"']?[^>]*content=[\"'][^\"']*?url=([^\"'>\s]+)",
                    html,
                    re.I,
                )
                if meta:
                    target = urljoin(final_url, meta.group(1).strip("\"'"))
                    try:
                        redirected = self.http.get(target)
                        if redirected.status_code == 200:
                            final_url = redirected.final_url or target
                            html = redirected.text or ""
                    except NetworkError:
                        pass

                ip = (
                    self.ip_override
                    or _config_value(html, "ip")
                    or _query_value(final_url, "wlanuserip")
                    or _query_value(final_url, "userip")
                    or _query_value(final_url, "ip")
                    or _local_ip_for(final_url)
                )
                if ip and not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
                    ip = None
                ac_id = (
                    _config_value(html, "acid")
                    or _query_value(final_url, "ac_id")
                    or context.ac_id
                    or self.cfg.ac_id
                    or "1"
                )
                page = PageProbe(
                    url=url,
                    final_url=final_url,
                    status_code=response.status_code,
                    headers=response.headers,
                    text=html,
                )
                return PortalContext(
                    protocol=self.protocol,
                    portal_url=final_url,
                    base=_origin(final_url),
                    ip=ip,
                    ac_id=str(ac_id),
                    referer=final_url,
                    page=page,
                    prepared=bool(ip),
                )
            except (NetworkError, PortalProtocolError) as exc:
                last_error = exc
                continue

        if last_error:
            raise NetworkError(f"无法打开 Srun 登录页：{last_error}")
        raise NetworkError("没有可用的 Srun 登录页地址")

    # -- 状态 ---------------------------------------------------------------

    def _user_info(self, base: str, referer: Optional[str] = None) -> Optional[dict]:
        data = self.http.get_jsonp(
            base,
            "/cgi-bin/rad_user_info",
            headers={"Referer": referer or base.rstrip("/") + "/"},
        )
        error = str(data.get("error", "")).strip()
        if error.lower() == "ok":
            return data if data.get("user_name") else None
        low = error.lower()
        if "not_online" in low or "not_found" in low or "logout" in low:
            return None
        raise PortalProtocolError(
            f"门户状态接口返回错误：{data.get('error_msg') or error or '未知'}"
        )

    def status(self, context: PortalContext) -> Status:
        last_error: Optional[Exception] = None
        for base in self._candidate_bases(context):
            try:
                info = self._user_info(base, context.referer)
                return Status(True, info) if info else Status(False)
            except (NetworkError, PortalProtocolError) as exc:
                last_error = exc
                continue

        internet = self.http.check_internet(self.cfg.connectivity_urls)
        if internet is True:
            return Status(True, {"connectivity_only": True})
        if internet is False:
            return Status(False)
        return Status(
            None,
            None,
            f"无法访问 Srun 状态接口：{last_error}" if last_error else "无法判断状态",
        )

    # -- 登录 ---------------------------------------------------------------

    def login(
        self, context: PortalContext, username: str, password: str
    ) -> LoginResult:
        context = self.prepare(context)
        ip = self.ip_override or context.ip or _local_ip_for(context.base)
        if not ip:
            raise PortalProtocolError("无法确定本机在校园网中的 IP，可用 --ip 手动指定")

        referer = context.referer or context.portal_url or context.base
        challenge = self.http.get_jsonp(
            context.base,
            "/cgi-bin/get_challenge",
            params={"username": username, "ip": ip},
            headers={"Referer": referer},
        )
        token = challenge.get("challenge")
        if str(challenge.get("error", "")).lower() != "ok" or not token:
            message = (
                challenge.get("error_msg")
                or challenge.get("error")
                or "未取得登录令牌"
            )
            raise PortalProtocolError(f"门户未返回 challenge：{message}")

        ip = str(
            self.ip_override
            or context.ip
            or challenge.get("client_ip")
            or ip
        )
        ac_id = str(context.ac_id or self.cfg.ac_id or "1")
        hmd5, info, chksum = build_auth_payload(
            username=username,
            password=password,
            ip=ip,
            ac_id=ac_id,
            token=str(token),
        )
        params = {
            "action": "login",
            "username": username,
            "password": "{MD5}" + hmd5,
            "ac_id": ac_id,
            "ip": ip,
            "chksum": chksum,
            "info": info,
            "n": "200",
            "type": "1",
            "os": "Windows 10",
            "name": "Windows",
            "double_stack": "0",
        }
        data = self.http.get_jsonp(
            context.base,
            "/cgi-bin/srun_portal",
            params=params,
            headers={"Referer": referer},
        )
        return self._evaluate_login(data, ip)

    @staticmethod
    def _evaluate_login(data: dict, ip: str) -> LoginResult:
        error = str(data.get("error", "") or "").strip()
        suc_msg = str(data.get("suc_msg", "") or "").strip()
        res = str(data.get("res", "") or "").strip()
        ecode = str(data.get("ecode", "") or "").strip()
        message = (
            data.get("error_msg")
            or data.get("suc_msg")
            or data.get("error")
            or "未知错误"
        )
        combined = f"{error} {suc_msg} {res} {message} {ecode}".lower()

        if "ip_already_online" in combined or "already_online" in combined:
            return LoginResult(
                ok=True,
                already_online=True,
                message="当前 IP 已在线",
                ip=ip,
                raw=data,
            )

        if ecode == "E2620" or "exceed" in combined or "在线数" in message:
            raise LimitError(str(message), ecode)
        if (
            ecode in ("E2553", "E2531")
            or "password_error" in combined
            or "user_not_found" in combined
            or "username_error" in combined
            or "密码" in message
            or "账号不存在" in message
            or "用户不存在" in message
        ):
            raise CredentialError(str(message), ecode)

        if error.lower() == "ok" or suc_msg.lower() == "login_ok" or res.lower() == "ok":
            return LoginResult(
                ok=True,
                message=suc_msg or "登录成功",
                ip=ip,
                raw=data,
            )
        raise LoginError(str(message), ecode)

    def verify(
        self, context: PortalContext, username: str, tries: int = 3
    ) -> Optional[dict]:
        for attempt in range(tries):
            for base in self._candidate_bases(context):
                try:
                    info = self._user_info(base, context.referer)
                except (NetworkError, PortalProtocolError):
                    info = None
                if info and info.get("user_name") == username:
                    return info
            if attempt < tries - 1:
                time.sleep(1.0)
        return None
