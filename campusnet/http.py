# -*- coding: utf-8 -*-
"""HTTP 封装：统一超时、错误、JSONP 解析和联网状态检测。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests

from .errors import NetworkError, PortalProtocolError
from .models import PageProbe

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

MAX_PAGE_BYTES = 2 * 1024 * 1024


@dataclass
class HttpResponse:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    text: str
    content: bytes = b""
    history: list[tuple[int, str]] = field(default_factory=list)

    def to_probe(self, via: str = "") -> PageProbe:
        return PageProbe(
            url=self.url,
            final_url=self.final_url,
            status_code=self.status_code,
            headers=self.headers,
            text=self.text,
            via=via,
        )


class HttpClient:
    def __init__(
        self,
        timeout: float = 8.0,
        verify_tls: bool = True,
        user_agent: str = USER_AGENT,
    ):
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        # 校园网认证直连门户，不把 HTTP(S)_PROXY 环境变量带进认证请求。
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": (
                    "text/html,application/json,application/javascript,"
                    "*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            }
        )

    # -- 原始请求 ------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        data=None,
        headers: Optional[dict] = None,
        allow_redirects: bool = True,
        timeout: Optional[float] = None,
        max_bytes: int = MAX_PAGE_BYTES,
    ) -> HttpResponse:
        read_timeout = timeout if timeout is not None else self.timeout
        request_timeout = (min(4.0, read_timeout), read_timeout)
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=request_timeout,
                verify=self.verify_tls,
                allow_redirects=allow_redirects,
                stream=True,
            )
        except requests.RequestException as exc:
            raise NetworkError(
                f"请求 {url} 失败（{exc.__class__.__name__}）"
            ) from exc

        try:
            raw = response.raw.read(max_bytes + 1, decode_content=True) or b""
        finally:
            response.close()

        truncated = len(raw) > max_bytes
        content = raw[:max_bytes]
        response._content = content

        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        try:
            text = response.text
        except Exception:
            text = content.decode("utf-8", errors="replace")
        if truncated:
            text += "\n<!-- truncated -->"

        return HttpResponse(
            url=url,
            final_url=response.url or url,
            status_code=response.status_code,
            headers={k.lower(): v for k, v in response.headers.items()},
            text=text,
            content=content,
            history=[
                (item.status_code, item.url) for item in response.history
            ],
        )

    def get(self, url: str, **kwargs) -> HttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> HttpResponse:
        return self.request("POST", url, **kwargs)

    def get_page(self, url: str, via: str = "", **kwargs) -> PageProbe:
        return self.get(url, **kwargs).to_probe(via=via)

    # -- 解析 ----------------------------------------------------------------

    @staticmethod
    def parse_json_or_jsonp(text: str) -> dict:
        content = (text or "").strip()
        if not content:
            raise PortalProtocolError("门户返回了空响应")
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        left = content.find("(")
        right = content.rfind(")")
        if left != -1 and right > left:
            inner = content[left + 1 : right].strip()
            try:
                parsed = json.loads(inner)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        raise PortalProtocolError("门户响应不是有效的 JSON/JSONP")

    def get_jsonp(
        self,
        base: str,
        path: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        query = dict(params or {})
        query["callback"] = "cb"
        query["_"] = str(int(time.time() * 1000))
        response = self.get(
            base.rstrip("/") + path,
            params=query,
            headers=headers,
        )
        if response.status_code != 200:
            raise NetworkError(f"门户返回 HTTP {response.status_code}")
        return self.parse_json_or_jsonp(response.text)

    # -- 联网状态 ------------------------------------------------------------

    @staticmethod
    def _is_success_body(url: str, text: str) -> bool:
        low_url = url.lower()
        body = (text or "").strip()
        low = body.lower()
        if "msftconnecttest" in low_url and "microsoft connect test" in low:
            return True
        if "captive.apple.com" in low_url and "success" in low:
            return True
        if "generate_204" in low_url and not body:
            return True
        if not body:
            return True
        if "<html" in low or "<form" in low or "<script" in low:
            return False
        return True

    def check_internet(self, urls: list[str]) -> Optional[bool]:
        """True=能上网，False=被劫持/需要认证，None=无法判断。"""
        unknown = False
        for url in urls:
            try:
                response = self.request(
                    "GET",
                    url,
                    allow_redirects=False,
                    timeout=min(4.0, self.timeout),
                    max_bytes=64 * 1024,
                )
            except NetworkError:
                unknown = True
                continue

            if response.status_code == 204:
                return True
            if 300 <= response.status_code < 400:
                return False
            if response.status_code == 200:
                if self._is_success_body(url, response.text):
                    return True
                return False
            # 403/404 等：继续尝试其它探测地址
            unknown = True
        _ = unknown
        return None

    def find_captive_portals(self, urls: list[str]) -> list[str]:
        """从联网探测地址的重定向/返回页里提取候选门户地址。"""
        found: list[str] = []

        def add(value: str):
            value = (value or "").strip()
            if value and value not in found:
                found.append(value)

        for url in urls:
            try:
                response = self.request(
                    "GET",
                    url,
                    allow_redirects=False,
                    timeout=min(4.0, self.timeout),
                    max_bytes=256 * 1024,
                )
            except NetworkError:
                continue

            if 300 <= response.status_code < 400:
                location = response.headers.get("location", "")
                if location:
                    add(urljoin(url, location))
                continue
            if response.status_code == 200:
                if self._is_success_body(url, response.text):
                    continue
                text = response.text or ""
                low = text.lower()
                if (
                    "<html" in low
                    or "<form" in low
                    or "srun" in low
                    or "eportal" in low
                    or "drcom" in low
                    or "校园网" in text
                ):
                    add(response.final_url or url)
        return found
