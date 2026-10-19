# -*- coding: utf-8 -*-
"""共享数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit


DEFAULT_CONNECTIVITY_URLS = [
    "http://connectivitycheck.gstatic.com/generate_204",
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://connect.rom.miui.com/generate_204",
    "http://captive.apple.com/hotspot-detect.html",
]


@dataclass
class Config:
    # 账号
    username: str = ""
    password: str = ""  # 可选，明文写在 config 里时使用；推荐留空
    remember_password: bool = True

    # 门户
    protocol: str = "auto"  # auto / srun / ruijie / generic
    url: str = ""  # 首次向导确认后的登录页地址
    # Srun 备用地址（可选）
    hosts: list[str] = field(default_factory=list)
    ac_id: str = "1"
    theme: str = "pro"

    # Ruijie ePortal
    service: str = ""
    query_string: str = ""

    # 通用表单
    password_encoding: str = "auto"  # auto / plain / md5 / sha1 / base64
    username_field: str = ""
    password_field: str = ""
    login_url: str = ""
    extra_fields: dict[str, str] = field(default_factory=dict)
    success_keywords: list[str] = field(default_factory=list)

    # 网络
    connectivity_urls: list[str] = field(
        default_factory=lambda: list(DEFAULT_CONNECTIVITY_URLS)
    )
    timeout: float = 8.0
    verify_tls: bool = True
    allow_cross_origin: bool = False

    # 守护
    interval: int = 30
    log_enabled: bool = True

    def is_configured(self) -> bool:
        """是否已经有足够的信息尝试登录（密码可另外提供）。"""
        url = (self.url or "").strip()
        return bool(self.username and url and url.lower() != "auto")

    @property
    def protocol_label(self) -> str:
        return {
            "srun": "深澜 Srun",
            "ruijie": "锐捷 ePortal",
            "generic": "通用网页表单",
            "auto": "自动识别",
        }.get(self.protocol, self.protocol)


@dataclass
class PageProbe:
    """一次门户页面探测的结果（脱敏后即可用于 issue 报告）。"""

    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    text: str
    via: str = ""

    def origin(self) -> str:
        parts = urlsplit(self.final_url or self.url)
        if not parts.scheme or not parts.netloc:
            return (self.final_url or self.url).rstrip("/")
        return f"{parts.scheme}://{parts.netloc}"


@dataclass
class PortalContext:
    """某个协议适配器完成准备后的登录上下文。"""

    protocol: str
    portal_url: str
    base: str
    ip: Optional[str] = None
    ac_id: Optional[str] = None
    referer: Optional[str] = None
    page: Optional[PageProbe] = None
    extras: dict = field(default_factory=dict)
    prepared: bool = False


@dataclass
class Detection:
    protocol: str
    display_name: str
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.score >= 0.6


@dataclass
class PortalCandidate:
    page: PageProbe
    detection: Detection
    source: str


@dataclass
class LoginResult:
    ok: bool
    message: str = ""
    already_online: bool = False
    raw: Optional[dict] = None
    ip: str = ""


def format_bytes(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


def format_online(data: Optional[dict]) -> str:
    if not data:
        return "离线"
    if data.get("connectivity_only"):
        return "已能访问互联网"

    parts = []
    user = data.get("user_name")
    ip = data.get("online_ip")
    if user:
        parts.append(f"账号 {user}")
    if ip:
        parts.append(f"IP {ip}")
    product = " / ".join(
        item for item in (data.get("products_name"), data.get("billing_name")) if item
    )
    if product:
        parts.append(product)
    if data.get("all_bytes") is not None:
        parts.append(f"本次流量 {format_bytes(data.get('all_bytes'))}")
    devices = data.get("online_device_total")
    if devices:
        parts.append(f"在线设备 {devices} 台")
    return " | ".join(parts) if parts else "已在线"
