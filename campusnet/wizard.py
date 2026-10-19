# -*- coding: utf-8 -*-
"""首次运行 / 更换门户的交互式向导。"""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

from .adapters import adapter_class
from .config import Config, save_config
from .credentials import CredentialStore
from .detector import (
    describe_candidate,
    discover_candidates,
    probe_portal,
)
from .errors import (
    CaptchaRequiredError,
    CredentialError,
    LimitError,
    LoginError,
    NetworkError,
    PortalProtocolError,
    UnsupportedPortalError,
)
from .http import HttpClient
from .models import PortalCandidate, PortalContext
from .runtime import safe_print


def is_interactive() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout)
    except Exception:
        return False


def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        safe_print("")
        return default
    return value or default


def prompt_password(confirm: bool = True) -> Optional[str]:
    while True:
        try:
            password = getpass.getpass("请输入校园网密码（输入时不显示）：")
        except (EOFError, KeyboardInterrupt):
            safe_print("")
            return None
        if not password:
            safe_print("密码不能为空，请重新输入。")
            continue
        if confirm:
            try:
                again = getpass.getpass("请再次输入以确认：")
            except (EOFError, KeyboardInterrupt):
                safe_print("")
                return None
            if password != again:
                safe_print("两次输入不一致，请重新输入。")
                continue
        return password


def _same_origin(a: str, b: str) -> bool:
    pa = urlsplit(a)
    pb = urlsplit(b)
    return (
        pa.scheme.lower() == pb.scheme.lower()
        and pa.netloc.lower() == pb.netloc.lower()
    )


def _choose_candidate(candidates: list[PortalCandidate]) -> Optional[PortalCandidate]:
    if not candidates:
        return None
    safe_print("\n检测到以下登录入口：")
    for index, candidate in enumerate(candidates, start=1):
        safe_print(f"  {index}. {describe_candidate(candidate)}")
        for reason in candidate.detection.reasons[:4]:
            safe_print(f"      - {reason}")
    safe_print("  m. 手动输入登录页网址")
    safe_print("  q. 取消")
    for _ in range(20):
        choice = prompt("请选择", "1").lower()
        if choice in ("q", "quit", "exit"):
            return None
        if choice in ("m", "manual"):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
        safe_print("请输入列表中的编号、m 或 q。")
    safe_print("输入次数过多，已取消。")
    return None


def _manual_candidate(http: HttpClient) -> Optional[PortalCandidate]:
    safe_print("\n请输入校园网登录页地址（未认证时浏览器打开的那个页面）。")
    for attempt in range(3):
        url = prompt("登录页网址")
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        candidate = probe_portal(http, url, via="手动输入", timeout=8)
        if candidate:
            return candidate
        safe_print(
            "没有在这个页面里识别到账号/密码表单。"
            "请确认它不是在浏览器里通过纯 JavaScript 渲染的页面，"
            "或者试试复制浏览器地址栏里的完整网址。"
        )
        if attempt < 2 and prompt("再试一次？[Y/n]", "y").lower() in ("y", "yes", ""):
            continue
        break
    return None


def _warnings_and_overrides(
    cfg: Config,
    protocol: str,
    context: PortalContext,
) -> list[str]:
    warnings: list[str] = []
    if protocol == "generic":
        selection = context.extras.get("selection")
        if context.extras.get("captcha"):
            warnings.append("页面需要验证码：自动登录不可用。")
        encoding = str(context.extras.get("encoding") or "")
        if encoding == "unsupported":
            warnings.append(
                "页面使用前端 RSA/AES 等密码加密：当前版本无法自动登录。"
            )
        if selection is not None:
            action = urljoin(
                context.portal_url,
                cfg.login_url or selection.form.action or context.portal_url,
            )
            if not _same_origin(action, context.base) and not cfg.allow_cross_origin:
                warnings.append(f"登录表单提交到不同域名：{action}")
                if prompt(
                    "确认允许把账号密码提交到这个地址吗？[y/N]", "n"
                ).lower() in ("y", "yes"):
                    cfg.allow_cross_origin = True
            if selection.otp_fields:
                warnings.append("页面需要短信/动态验证码：自动登录不可用。")
        if encoding == "plain" and not warnings:
            warnings.append("使用明文表单提交密码（这是该门户页面的正常方式）。")
    elif protocol == "ruijie":
        if context.extras.get("captcha"):
            warnings.append("ePortal 页面需要验证码：自动登录不可用。")
        if context.extras.get("encryption_required"):
            warnings.append(
                "ePortal 使用前端加密密码（passwordEncrypt=true）："
                "当前版本无法自动登录。"
            )
    return warnings


def _save_detected_config(
    cfg: Config,
    protocol: str,
    context: PortalContext,
) -> None:
    cfg.protocol = protocol
    if protocol == "srun":
        # 保存稳定基地址，运行时重新拉取带 ac_id/theme 的登录页。
        cfg.url = context.base
        if cfg.url not in cfg.hosts:
            cfg.hosts.insert(0, cfg.url)
        if context.ac_id:
            cfg.ac_id = str(context.ac_id)
    elif protocol == "ruijie":
        # ePortal 的 queryString 与本次会话相关，保存基地址，运行时重新取。
        cfg.url = context.base
        if context.extras.get("service"):
            cfg.service = str(context.extras["service"])
    else:
        cfg.url = context.portal_url
        selection = context.extras.get("selection")
        if selection is not None:
            cfg.username_field = selection.username_field.name
            cfg.password_field = selection.password_field.name


def run_setup_wizard(
    cfg: Config,
    cfg_path: Path,
    http: HttpClient,
    logger: logging.Logger,
    store: CredentialStore,
    protocol_preference: str = "auto",
) -> int:
    if not is_interactive():
        safe_print("首次设置需要在终端窗口中运行。")
        return 2

    safe_print("=" * 58)
    safe_print("  CampusNet Login 设置向导")
    safe_print("=" * 58)
    safe_print("只会向你在下一步确认过的校园网登录页发送账号密码。")
    safe_print("自动探测阶段只发送普通页面请求，不会发送任何认证信息。")
    if cfg.password:
        safe_print("注意：config.ini 中当前有明文密码，完成设置后会改为系统安全存储。")
    safe_print("")

    candidates = discover_candidates(cfg, http, logger)
    candidate: Optional[PortalCandidate]
    if candidates:
        candidate = _choose_candidate(candidates)
    else:
        safe_print("没有自动检测到门户入口。")
        candidate = None

    if candidate is None:
        candidate = _manual_candidate(http)
    if candidate is None:
        safe_print("已取消设置。")
        return 2

    protocol = (
        protocol_preference
        if protocol_preference and protocol_preference != "auto"
        else candidate.detection.protocol
    )
    cls = adapter_class(protocol)
    if cls is None:
        safe_print(f"不支持的协议：{protocol}")
        return 2

    page = candidate.page
    safe_print("")
    safe_print(f"登录页：{page.final_url or page.url}")
    safe_print(f"识别为：{cls.display_name}")
    if candidate.detection.reasons:
        safe_print("依据：" + "；".join(candidate.detection.reasons))
    safe_print("")
    safe_print(
        "请确认这是你们学校官方的校园网登录页。"
        "如果这是陌生网络自动跳转出来的页面，请不要继续。"
    )
    if prompt("使用这个登录页？[Y/n]", "y").lower() not in ("y", "yes", ""):
        safe_print("已取消设置。")
        return 2

    # 切换门户时清掉只属于上一种协议/上一个门户的覆盖项，避免旧字段影响新登录。
    cfg.allow_cross_origin = False
    cfg.password_encoding = "auto"
    cfg.login_url = ""
    cfg.username_field = ""
    cfg.password_field = ""
    cfg.service = ""

    adapter = cls(cfg, http, logger)
    try:
        context = adapter.context_from_page(cfg, page)
        context = adapter.prepare(context)
    except (NetworkError, PortalProtocolError, UnsupportedPortalError) as exc:
        safe_print(f"无法准备登录参数：{exc}")
        return 1

    warnings = _warnings_and_overrides(cfg, protocol, context)
    if warnings:
        safe_print("\n注意事项：")
        for item in warnings:
            safe_print(f"  - {item}")
        if any("自动登录不可用" in item for item in warnings):
            if prompt("仍然保存这个门户的配置和密码吗？[Y/n]", "y").lower() not in (
                "y",
                "yes",
                "",
            ):
                safe_print("已取消设置。")
                return 2

    safe_print("")
    username = prompt("校园网账号", cfg.username).strip()
    if not username:
        safe_print("账号不能为空，设置取消。")
        return 2
    password = prompt_password(confirm=True)
    if not password:
        safe_print("未输入密码，设置取消。")
        return 2

    cfg.username = username
    cfg.password = ""  # 密码改由系统安全存储保存，避免 config.ini 里残留明文
    _save_detected_config(cfg, protocol, context)

    save_choice = cfg.remember_password
    if prompt("用系统安全存储保存密码？[Y/n]", "y" if save_choice else "n").lower() in (
        "y",
        "yes",
        "",
    ):
        cfg.remember_password = True
    else:
        cfg.remember_password = False

    try:
        save_config(cfg_path, cfg)
        safe_print(f"配置已保存：{cfg_path}")
    except OSError as exc:
        safe_print(f"保存配置失败：{exc}")
        return 1

    if cfg.remember_password:
        try:
            store.save(username, password)
            safe_print(f"密码已保存（{store.backend}）。")
        except Exception as exc:
            safe_print(f"密码保存失败：{exc}")
            cfg.remember_password = False
    else:
        store.delete(username)
        safe_print("未保存密码，以后每次运行会提示输入。")

    safe_print("")
    safe_print("正在检查当前网络并测试登录……")
    try:
        status = adapter.status(context)
    except (NetworkError, PortalProtocolError) as exc:
        safe_print(f"暂时无法检查在线状态：{exc}")
        safe_print("配置已保存；联网后运行一键联网即可验证。")
        return 0

    if status.online is True:
        safe_print("当前已经在线，密码未经过验证（配置已保存）。")
        return 0

    try:
        result = adapter.login(context, username, password)
    except CredentialError as exc:
        if cfg.remember_password:
            store.delete(username)
        safe_print(f"密码验证失败：{exc}")
        safe_print("已删除保存的密码。请确认密码后重新运行 --setup。")
        return 3
    except (CaptchaRequiredError, UnsupportedPortalError) as exc:
        safe_print(f"门户暂不支持自动登录：{exc}")
        safe_print("配置和密码已保留；后续版本可能会支持。")
        return 0
    except LimitError as exc:
        safe_print(f"门户限制登录：{exc}")
        safe_print("配置已保存；处理在线设备数后可直接使用。")
        return 4
    except (NetworkError, PortalProtocolError, LoginError) as exc:
        safe_print(f"登录测试未完成：{exc}")
        safe_print("配置已保存；可以稍后重试，或运行 --diagnose 查看详情。")
        return 0

    safe_print("登录请求成功。")
    verified = adapter.verify(context, username, tries=3)
    if verified:
        safe_print("已确认可以上网。设置完成。")
    else:
        safe_print("门户已接受登录，稍后会自动确认网络状态。设置完成。")
    return 0
