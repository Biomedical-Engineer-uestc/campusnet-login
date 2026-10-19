# -*- coding: utf-8 -*-
"""校园网登录门户自动探测。

只发送普通 GET 请求，不发送任何账号密码。最终使用哪个地址必须由用户在
首次运行向导里确认。
"""

from __future__ import annotations

import locale
import logging
import re
import subprocess
import sys
from typing import Optional
from urllib.parse import urljoin

from .adapters import best_detection, detect_protocols
from .config import Config
from .errors import NetworkError
from .http import HttpClient
from .models import PageProbe, PortalCandidate
from .runtime import IS_WINDOWS

COMMON_GATEWAYS = ("192.168.1.1", "192.168.0.1", "10.0.0.1")
_SOURCE_PRIORITY = {
    "已保存": 0,
    "校园网重定向": 1,
    "Srun 登录页": 2,
    "配置的地址": 2,
    "默认网关": 3,
    "常见网关": 4,
    "手动输入": 0,
}


def _ipv4(value: str) -> Optional[str]:
    match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", value or "")
    if not match:
        return None
    ip = match.group(1)
    if ip == "0.0.0.0":
        return None
    return ip


def default_gateways() -> list[str]:
    gateways: list[str] = []
    try:
        if IS_WINDOWS:
            encoding = locale.getpreferredencoding(False)
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                encoding=encoding,
                errors="replace",
                timeout=5,
            )
            for line in (result.stdout or "").splitlines():
                low = line.lower()
                if "default gateway" in low or "默认网关" in line:
                    ip = _ipv4(line.split(":", 1)[-1])
                    if ip and ip not in gateways:
                        gateways.append(ip)
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["netstat", "-rn"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            for line in (result.stdout or "").splitlines():
                if line.strip().startswith("default"):
                    parts = line.split()
                    if len(parts) >= 2:
                        ip = _ipv4(parts[1])
                        if ip and ip not in gateways:
                            gateways.append(ip)
        else:
            result = subprocess.run(
                ["ip", "route"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            for line in (result.stdout or "").splitlines():
                if line.startswith("default via "):
                    ip = _ipv4(line.split()[2])
                    if ip and ip not in gateways:
                        gateways.append(ip)
    except (OSError, subprocess.SubprocessError):
        pass
    return gateways


def _candidate_sources(
    cfg: Config, http: HttpClient
) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []

    def add(url: str, source: str):
        url = (url or "").strip()
        if not url or url.lower() == "auto":
            return
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        item = (url, source)
        if item not in sources:
            sources.append(item)

    try:
        for portal in http.find_captive_portals(cfg.connectivity_urls):
            add(portal, "校园网重定向")
    except Exception:
        pass

    if cfg.url:
        add(cfg.url, "已保存")
    for host in cfg.hosts:
        add(host, "配置的地址")

    # 旧配置/手动配置可能只保存了 Srun 门户基地址，直接拼出登录页再探测。
    if cfg.protocol == "srun":
        from urllib.parse import quote

        bases = []
        if cfg.url:
            bases.append(cfg.url)
        bases.extend(cfg.hosts)
        for base in bases:
            add(
                base.rstrip("/")
                + "/srun_portal_pc?ac_id="
                + quote(cfg.ac_id or "1")
                + "&theme="
                + quote(cfg.theme or "pro"),
                "Srun 登录页",
            )

    for gateway in default_gateways():
        add(f"http://{gateway}", "默认网关")
    for gateway in COMMON_GATEWAYS:
        add(f"http://{gateway}", "常见网关")

    return sources


def _maybe_client_redirect(page: PageProbe) -> Optional[str]:
    html = page.text or ""
    for pattern in (
        r"window\.location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']",
        r"location\.replace\(\s*[\"']([^\"']+)[\"']\s*\)",
        r"location\.href\s*=\s*[\"']([^\"']+)[\"']",
        r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"'][^\"']*?url=([^\"'>\s]+)",
    ):
        match = re.search(pattern, html, re.I)
        if match:
            return urljoin(page.final_url or page.url, match.group(1).strip("\"'"))
    return None


def probe_portal(
    http: HttpClient,
    url: str,
    via: str = "手动输入",
    timeout: float = 4.0,
) -> Optional[PortalCandidate]:
    try:
        page = http.get_page(url, via=via, timeout=timeout)
    except NetworkError:
        return None
    if page.status_code != 200 or not page.text:
        return None

    detection = best_detection(page)
    if detection is None:
        redirect = _maybe_client_redirect(page)
        if redirect and redirect != (page.final_url or page.url):
            try:
                redirected = http.get_page(redirect, via=via, timeout=timeout)
            except NetworkError:
                return None
            detection = best_detection(redirected)
            if detection is not None:
                page = redirected
    if detection is None:
        return None
    return PortalCandidate(page=page, detection=detection, source=via)


def discover_candidates(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    timeout: float = 3.0,
) -> list[PortalCandidate]:
    candidates: list[PortalCandidate] = []
    seen_urls: set[str] = set()
    sources = _candidate_sources(cfg, http)
    gateway_sources = {"默认网关", "常见网关"}
    primary = [(url, source) for url, source in sources if source not in gateway_sources]
    fallback = [(url, source) for url, source in sources if source in gateway_sources]

    def probe_group(group):
        for url, source in group:
            probe_timeout = timeout if source in gateway_sources else cfg.timeout
            candidate = probe_portal(http, url, via=source, timeout=probe_timeout)
            if candidate is None:
                continue
            key = candidate.page.final_url or candidate.page.url
            if key in seen_urls:
                continue
            seen_urls.add(key)
            candidates.append(candidate)

    probe_group(primary)
    # 已有保存/重定向/配置入口时不再扫描网关，既快也避免误把家用路由器当成门户。
    if not candidates:
        probe_group(fallback)

    candidates.sort(
        key=lambda c: (
            _SOURCE_PRIORITY.get(c.source, 9),
            -c.detection.score,
        )
    )
    return candidates


def describe_candidate(candidate: PortalCandidate) -> str:
    page = candidate.page
    return (
        f"[{candidate.detection.display_name}] "
        f"{page.final_url or page.url}（{candidate.source}）"
    )


def format_detection_details(page: PageProbe) -> list[str]:
    lines = []
    for detection in detect_protocols(page):
        lines.append(
            f"- {detection.display_name}: {detection.score:.2f} "
            f"{'；'.join(detection.reasons) if detection.reasons else ''}"
        )
    return lines
