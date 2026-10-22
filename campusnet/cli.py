# -*- coding: utf-8 -*-
"""命令行入口：首次设置、连接、守护、状态、探测和诊断。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .adapters import PROTOCOL_CHOICES, ProtocolAdapter, Status, adapter_class, best_detection
from .config import Config, ensure_config, load_config
from .credentials import CredentialStore
from .detector import (
    describe_candidate,
    discover_candidates,
    format_detection_details,
    probe_portal,
)
from .errors import (
    CaptchaRequiredError,
    ConfigError,
    CredentialError,
    LimitError,
    LoginError,
    NetworkError,
    PortalProtocolError,
    UnsupportedPortalError,
)
from .http import HttpClient
from .models import LoginResult, PortalContext, format_online
from .runtime import (
    DATA_DIR,
    IS_WINDOWS,
    LOG_PATH,
    consume_stop_request,
    request_stop,
    resolve_config_path,
    safe_print,
    show_message,
    sleep_with_stop,
    watch_is_running,
)
from . import wizard
from .wizard import is_interactive


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def setup_logging(cfg: Config, quiet: bool, verbose: bool) -> logging.Logger:
    logger = logging.getLogger("campusnet")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    level = logging.DEBUG if verbose else logging.INFO

    if cfg.log_enabled:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            logger.addHandler(file_handler)
        except OSError:
            pass

    if sys.stdout is not None and not quiet:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def _plain_logger(quiet: bool = False) -> logging.Logger:
    logger = logging.getLogger("campusnet.simple")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    if sys.stdout is not None and not quiet:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    else:
        logger.addHandler(logging.NullHandler())
    return logger


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------


def resolve_password(
    cfg: Config,
    store: CredentialStore,
    logger: logging.Logger,
    allow_prompt: bool,
) -> tuple[Optional[str], str]:
    if cfg.password:
        logger.warning(
            "config.ini 中填写了明文密码；建议清空并改用 --setup 加密保存。"
        )
        return cfg.password, "config"

    env_password = os.environ.get("CAMPUSNET_PASSWORD")
    if env_password:
        return env_password, "env"

    if cfg.username:
        stored = store.load(cfg.username)
        if stored:
            return stored, "store"

    if allow_prompt and is_interactive():
        password = wizard.prompt_password(confirm=True)
        if password:
            return password, "prompt"
    return None, "none"


# ---------------------------------------------------------------------------
# 运行时 / 适配器
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    protocol: str
    adapter: ProtocolAdapter
    context: Optional[PortalContext]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}"


def _hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _make_http(cfg: Config) -> HttpClient:
    return HttpClient(timeout=cfg.timeout, verify_tls=cfg.verify_tls)


def _make_adapter(
    cfg: Config,
    protocol: str,
    http: HttpClient,
    logger: logging.Logger,
    ip_override: Optional[str],
) -> ProtocolAdapter:
    cls = adapter_class(protocol)
    if cls is None:
        raise ConfigError(f"不支持的协议：{protocol}")
    return cls(cfg, http, logger, ip_override=ip_override)


def build_runtime(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    ip_override: Optional[str] = None,
    protocol: Optional[str] = None,
    url: Optional[str] = None,
) -> Runtime:
    protocol = protocol or cfg.protocol
    target = (url or cfg.url or "").strip()

    if protocol == "auto":
        if not target:
            raise ConfigError("尚未配置门户地址，请先运行 --setup")
        page = http.get_page(target, via="配置")
        detection = best_detection(page)
        if detection is None:
            raise UnsupportedPortalError(
                "无法识别这个登录页的协议。请确认地址是未认证时浏览器打开的页面，"
                "或运行 --diagnose 查看详情。"
            )
        protocol = detection.protocol

    if protocol not in ("srun", "ruijie", "generic"):
        raise ConfigError(f"不支持的协议：{protocol}")

    adapter = _make_adapter(cfg, protocol, http, logger, ip_override)

    if protocol == "srun":
        target = target or (cfg.hosts[0] if cfg.hosts else "")
        if not target:
            raise ConfigError("尚未配置 Srun 门户地址")
        allowed_hosts = {
            _hostname(item)
            for item in [target, cfg.url, *cfg.hosts]
            if item
        }
        context = PortalContext(
            protocol="srun",
            portal_url=target,
            base=_origin(target),
            referer=target,
            prepared=False,
        )
        context = adapter.prepare(context)
        if _hostname(context.base) not in allowed_hosts:
            raise NetworkError(
                "登录页被重定向到了未确认的域名，已停止发送认证信息。"
                "如果学校门户地址变更，请重新运行 --setup。"
            )
        return Runtime(protocol, adapter, context)

    if not target:
        raise ConfigError("尚未配置门户地址")
    target_host = _hostname(target)
    page = http.get_page(target, via="配置")
    if _hostname(page.final_url or page.url) != target_host:
        raise NetworkError(
            "登录页被重定向到了未确认的域名，已停止发送认证信息。"
            "当前 Wi-Fi 可能不是学校网络；如门户地址变更，请重新运行 --setup。"
        )
    context = type(adapter).context_from_page(cfg, page)
    context = adapter.prepare(context)
    if _hostname(context.base) != target_host:
        raise NetworkError(
            "登录页被重定向到了未确认的域名，已停止发送认证信息。"
            "当前 Wi-Fi 可能不是学校网络；如门户地址变更，请重新运行 --setup。"
        )
    return Runtime(protocol, adapter, context)


def check_status(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    ip_override: Optional[str] = None,
    quick: bool = False,
) -> tuple[Runtime, Status]:
    protocol = cfg.protocol

    if protocol == "auto":
        runtime = build_runtime(cfg, http, logger, ip_override=ip_override)
        return runtime, runtime.adapter.status(runtime.context)

    adapter = _make_adapter(cfg, protocol, http, logger, ip_override)

    if protocol == "srun":
        target = (cfg.url or (cfg.hosts[0] if cfg.hosts else "")).strip()
        if not target:
            raise ConfigError("尚未配置 Srun 门户地址")
        context = PortalContext(
            protocol="srun",
            portal_url=target,
            base=_origin(target),
            referer=target,
            prepared=False,
        )
        if quick:
            # 守护/一次性连接先看互联网是否可用。在别的 Wi-Fi 上可上网时
            # 不访问学校门户，避免每 30 秒对不存在的校园网做超时探测。
            internet = http.check_internet(cfg.connectivity_urls)
            if internet is True:
                return (
                    Runtime(protocol, adapter, context),
                    Status(True, {"connectivity_only": True}),
                )
        return Runtime(protocol, adapter, context), adapter.status(context)

    # 锐捷/通用表单：是否在线用联网探测判断，不需要每次拉取门户页。
    return Runtime(protocol, adapter, None), adapter.internet_status()


# ---------------------------------------------------------------------------
# 单次连接
# ---------------------------------------------------------------------------


def _save_prompted_password(
    cfg: Config,
    store: CredentialStore,
    logger: logging.Logger,
    username: str,
    password: str,
    source: str,
) -> None:
    if source == "prompt" and cfg.remember_password:
        try:
            store.save(username, password)
            logger.info("密码已用 %s 加密保存。", store.backend)
        except Exception as exc:
            logger.warning("密码保存失败（不影响联网）：%s", exc)
    elif source == "prompt":
        logger.info("未保存密码（remember_password = false）。")


def connect_once(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    store: CredentialStore,
    ip_override: Optional[str] = None,
) -> int:
    if not cfg.username:
        logger.error("还没有配置校园网账号，请先运行 --setup。")
        return 2

    try:
        runtime, status = check_status(
            cfg, http, logger, ip_override, quick=True
        )
    except (NetworkError, PortalProtocolError) as exc:
        logger.error("%s。请确认已连接校园网 Wi-Fi 或网线。", exc)
        return 1
    except (ConfigError, UnsupportedPortalError) as exc:
        logger.error("%s", exc)
        return 2

    if status.online is True:
        logger.info("当前已在线：%s", format_online(status.info))
        return 0
    if status.online is None:
        logger.info("暂时无法确认在线状态（%s），尝试登录。", status.message)

    try:
        runtime = build_runtime(cfg, http, logger, ip_override=ip_override)
    except (NetworkError, PortalProtocolError) as exc:
        logger.error("无法打开登录页：%s", exc)
        return 1
    except (ConfigError, UnsupportedPortalError) as exc:
        logger.error("%s", exc)
        return 2
    adapter, context = runtime.adapter, runtime.context

    password, source = resolve_password(cfg, store, logger, allow_prompt=True)
    if not password:
        logger.error("没有可用的密码。请先运行：python campus_net.py --setup")
        if not is_interactive():
            show_message(
                "CampusNet",
                "还没有保存校园网密码。\n\n"
                "请双击“一键联网.bat”，按提示完成首次设置。",
            )
        return 2

    prompt_retries = 0
    net_retries = 0
    result: Optional[LoginResult] = None
    while True:
        try:
            result = adapter.login(context, cfg.username, password)
            break
        except CredentialError as exc:
            logger.error("登录失败：%s", exc)
            if source == "store":
                store.delete(cfg.username)
                logger.error("已删除失效的本地密码，请运行 --setup 重新设置。")
            elif source == "config":
                logger.error("请检查 config.ini 中的 password，或清空它后改用 --setup。")
            elif source == "prompt" and prompt_retries < 2 and is_interactive():
                prompt_retries += 1
                logger.info("请重新输入密码（第 %d/%d 次机会）。", prompt_retries, 2)
                new_password = wizard.prompt_password(confirm=False)
                if new_password:
                    password = new_password
                    continue
            return 3
        except (CaptchaRequiredError, UnsupportedPortalError) as exc:
            logger.error("该门户暂不支持自动登录：%s", exc)
            return 4
        except LimitError as exc:
            logger.error("登录受限：%s", exc)
            return 4
        except LoginError as exc:
            logger.error("门户拒绝了登录：%s", exc)
            return 3
        except (NetworkError, PortalProtocolError) as exc:
            net_retries += 1
            if net_retries < 3:
                logger.warning("登录请求失败，正在重试（%d/3）：%s", net_retries, exc)
                time.sleep(2 * net_retries)
                continue
            logger.error("登录请求失败：%s", exc)
            return 1

    if result is not None and result.already_online:
        logger.info("门户提示当前 IP 已经在线。")
    else:
        logger.info("登录成功。")

    verified = adapter.verify(context, cfg.username, tries=3)
    if verified:
        logger.info("当前已在线：%s", format_online(verified))
    else:
        logger.info("门户已接受登录请求，在线状态稍后会生效。")

    _save_prompted_password(cfg, store, logger, cfg.username, password, source)
    return 0


# ---------------------------------------------------------------------------
# 守护模式
# ---------------------------------------------------------------------------


def _fatal_watch(
    logger: logging.Logger,
    quiet: bool,
    title: str,
    message: str,
) -> int:
    logger.error(message.replace("\n", " "))
    if quiet or not is_interactive():
        show_message(title, message)
    return 3


def run_watch(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    store: CredentialStore,
    quiet: bool,
    ip_override: Optional[str] = None,
) -> int:
    if not cfg.username:
        logger.error("还没有配置校园网账号。")
        show_message("CampusNet", "还没有完成首次设置，请先双击“一键联网.bat”。")
        return 2

    password, source = resolve_password(cfg, store, logger, allow_prompt=True)
    if not password:
        logger.error("没有可用的密码，守护模式无法启动。请先运行 --setup。")
        show_message(
            "CampusNet",
            "还没有保存校园网密码，自动重连未启动。\n\n"
            "请双击“一键联网.bat”完成首次设置。",
        )
        return 2

    if source == "prompt" and cfg.remember_password:
        try:
            store.save(cfg.username, password)
            logger.info("密码已用 %s 加密保存。", store.backend)
        except Exception as exc:
            logger.warning("密码保存失败：%s", exc)

    from .runtime import acquire_watch_mutex

    mutex = acquire_watch_mutex()
    if mutex is False:
        logger.info("已有守护进程在运行，本次启动退出。")
        return 0
    _ = mutex

    last_online: Optional[bool] = None
    last_error_message = ""
    last_error_at = 0.0
    login_error_count = 0
    failure_backoff = cfg.interval
    max_backoff = max(600, cfg.interval)

    logger.info("守护模式已启动，每 %d 秒检查一次。", cfg.interval)

    while True:
        if consume_stop_request():
            logger.info("收到停止请求，守护进程退出。")
            return 0

        cycle_failed = False
        try:
            runtime, status = check_status(
                cfg, http, logger, ip_override, quick=True
            )
            if status.online is True:
                if failure_backoff > cfg.interval:
                    logger.info(
                        "网络恢复正常，检查间隔恢复为 %d 秒。", cfg.interval
                    )
                failure_backoff = cfg.interval
                if last_online is not True:
                    logger.info("已在线：%s", format_online(status.info))
                last_online = True
                last_error_message = ""
                login_error_count = 0
            else:
                if status.online is None:
                    now = time.time()
                    message = status.message or "未知"
                    if message != last_error_message or now - last_error_at > 300:
                        logger.info("状态未知（%s），尝试登录。", message)
                        last_error_message = message
                        last_error_at = now
                elif last_online is not False:
                    logger.info("检测到未认证，正在登录……")
                last_online = False

                runtime = build_runtime(cfg, http, logger, ip_override=ip_override)
                adapter, context = runtime.adapter, runtime.context
                try:
                    result = adapter.login(context, cfg.username, password)
                    logger.info(
                        "登录成功%s",
                        "（当前 IP 已在线）" if result.already_online else "。",
                    )
                    if failure_backoff > cfg.interval:
                        logger.info(
                            "网络恢复正常，检查间隔恢复为 %d 秒。", cfg.interval
                        )
                    failure_backoff = cfg.interval
                    login_error_count = 0
                    last_online = True
                except CredentialError as exc:
                    if source == "store":
                        store.delete(cfg.username)
                    return _fatal_watch(
                        logger,
                        quiet,
                        "CampusNet 已停止",
                        f"校园网密码验证失败：{exc}\n\n"
                        "为避免反复输错导致账号被锁，自动重连已停止。\n"
                        "请双击“一键联网.bat”重新设置密码。",
                    )
                except (CaptchaRequiredError, UnsupportedPortalError) as exc:
                    return _fatal_watch(
                        logger,
                        quiet,
                        "CampusNet 已停止",
                        f"该校园网门户暂不支持自动登录：{exc}\n\n"
                        "请使用浏览器手动登录，或到项目主页提交适配需求。",
                    )
                except LimitError as exc:
                    return _fatal_watch(
                        logger,
                        quiet,
                        "CampusNet 已停止",
                        f"校园网登录受限：{exc}\n\n"
                        "请先处理在线设备数超限等问题，再重新启动。",
                    )
                except LoginError as exc:
                    login_error_count += 1
                    logger.warning(
                        "门户拒绝登录：%s（第 %d 次）", exc, login_error_count
                    )
                    if not getattr(exc, "retryable", True):
                        return _fatal_watch(
                            logger,
                            quiet,
                            "CampusNet 已停止",
                            f"门户拒绝了登录：{exc}",
                        )
                    if login_error_count >= 3:
                        return _fatal_watch(
                            logger,
                            quiet,
                            "CampusNet 已停止",
                            f"连续 3 次被门户拒绝：{exc}",
                        )
                except (NetworkError, PortalProtocolError) as exc:
                    cycle_failed = True
                    now = time.time()
                    message = str(exc)
                    if message != last_error_message or now - last_error_at > 300:
                        logger.warning("暂时无法登录：%s", exc)
                        last_error_message = message
                        last_error_at = now
        except (NetworkError, PortalProtocolError) as exc:
            cycle_failed = True
            now = time.time()
            message = str(exc)
            if message != last_error_message or now - last_error_at > 300:
                logger.warning("暂时无法访问门户（网络可能还没连上）：%s", exc)
                last_error_message = message
                last_error_at = now
            last_online = False
        except (ConfigError, UnsupportedPortalError) as exc:
            return _fatal_watch(
                logger,
                quiet,
                "CampusNet 已停止",
                f"配置或门户有问题：{exc}\n\n"
                "请运行 --setup 重新配置，或提交 issue 获取适配支持。",
            )
        except Exception as exc:
            cycle_failed = True
            logger.exception("守护循环出现未预期错误：%s", exc)

        if cycle_failed:
            sleep_seconds = failure_backoff
            next_backoff = min(max_backoff, max(cfg.interval, failure_backoff * 2))
            if next_backoff != failure_backoff:
                logger.info("连续失败，%d 秒后重试。", sleep_seconds)
            failure_backoff = next_backoff
        else:
            sleep_seconds = cfg.interval

        try:
            if sleep_with_stop(sleep_seconds):
                logger.info("收到停止请求，守护进程退出。")
                return 0
        except KeyboardInterrupt:
            logger.info("已停止守护进程。")
            return 0


# ---------------------------------------------------------------------------
# 状态 / 探测 / 诊断
# ---------------------------------------------------------------------------


def run_status(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    ip_override: Optional[str] = None,
) -> int:
    try:
        _, status = check_status(cfg, http, logger, ip_override)
    except (NetworkError, PortalProtocolError) as exc:
        logger.error("%s", exc)
        return 1
    except (ConfigError, UnsupportedPortalError) as exc:
        logger.error("%s", exc)
        return 2

    if status.online is True:
        logger.info("当前已在线：%s", format_online(status.info))
        return 0
    if status.online is False:
        logger.info("当前未认证（离线）。")
        return 2
    logger.info("无法判断在线状态：%s", status.message or "未知")
    return 1


def run_detect(
    cfg: Config,
    http: HttpClient,
    logger: logging.Logger,
    portal_override: Optional[str] = None,
) -> int:
    if portal_override:
        candidate = probe_portal(
            http, portal_override, via="手动输入", timeout=cfg.timeout
        )
        candidates = [candidate] if candidate else []
    else:
        candidates = discover_candidates(cfg, http, logger)

    if not candidates:
        safe_print("没有检测到可用的校园网登录页。")
        safe_print("如果当前已经能上网，请手动运行 --setup 并输入登录页地址。")
        return 2

    safe_print("检测到以下登录入口：")
    for index, candidate in enumerate(candidates, start=1):
        safe_print(f"{index}. {describe_candidate(candidate)}")
        for reason in candidate.detection.reasons:
            safe_print(f"   - {reason}")
        if logger.isEnabledFor(logging.DEBUG):
            for line in format_detection_details(candidate.page):
                safe_print(f"   {line}")
    return 0


def _mask_username(value: str) -> str:
    if not value:
        return "(未设置)"
    if len(value) <= 4:
        return "***"
    return value[:2] + "***" + value[-2:]


def _redact_query(url: str) -> str:
    try:
        keys = list(parse_qs(urlsplit(url).query).keys())
        return ", ".join(keys) if keys else "(无)"
    except Exception:
        return "(无法解析)"


def run_diagnose(
    cfg: Config,
    cfg_path: Path,
    http: HttpClient,
    logger: logging.Logger,
    ip_override: Optional[str] = None,
) -> int:
    safe_print(f"CampusNet Login {__version__}")
    safe_print(f"平台：{sys.platform} / Python {sys.version.split()[0]}")
    safe_print(f"配置文件：{cfg_path}")
    safe_print(f"协议：{cfg.protocol}")
    safe_print(f"门户：{cfg.url or '(未设置)'}")
    safe_print(f"账号：{_mask_username(cfg.username)}")
    safe_print(f"TLS 校验：{cfg.verify_tls}")
    safe_print(f"联网探测：{', '.join(cfg.connectivity_urls)}")
    safe_print("")

    try:
        _, status = check_status(cfg, http, logger, ip_override)
        if status.online is True:
            if status.info and status.info.get("connectivity_only"):
                safe_print("在线状态：在线（可访问互联网）")
            else:
                safe_print("在线状态：在线")
        elif status.online is False:
            safe_print("在线状态：离线/未认证")
        else:
            safe_print("在线状态：无法判断")
        if status.message:
            safe_print(f"状态说明：{status.message}")
    except Exception as exc:
        safe_print(f"状态检测失败：{exc.__class__.__name__}: {exc}")

    safe_print("")
    safe_print("门户探测（不发送任何账号密码）：")
    candidates = discover_candidates(cfg, http, logger, timeout=2.0)
    if not candidates:
        safe_print("  (没有检测到)")
    for candidate in candidates:
        safe_print(f"- {describe_candidate(candidate)}")
        for reason in candidate.detection.reasons:
            safe_print(f"    {reason}")

    if candidates and candidates[0].detection.protocol in ("generic", "ruijie"):
        page = candidates[0].page
        safe_print("")
        safe_print("页面参数（已脱敏）：")
        safe_print(f"  查询参数键：{_redact_query(page.final_url or page.url)}")
        try:
            cls = adapter_class(candidates[0].detection.protocol)
            adapter = cls(cfg, http, logger, ip_override)
            context = cls.context_from_page(cfg, page)
            if candidates[0].detection.protocol == "generic":
                selection = context.extras.get("selection")
                if selection:
                    safe_print(f"  账号字段：{selection.username_field.name}")
                    safe_print(f"  密码字段：{selection.password_field.name}")
                    safe_print(f"  表单 action：{selection.form.action or '(当前页)'}")
                safe_print(f"  密码编码：{context.extras.get('encoding')}")
                safe_print(f"  验证码：{context.extras.get('captcha')}")
            else:
                safe_print(f"  服务名：{context.extras.get('service') or '(空)'}")
                safe_print(
                    f"  前端加密：{context.extras.get('encryption_required')}"
                )
                safe_print(f"  验证码：{context.extras.get('captcha')}")
        except Exception as exc:
            safe_print(f"  参数解析失败：{exc.__class__.__name__}: {exc}")
    return 0


# ---------------------------------------------------------------------------
# 参数与入口
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CampusNet Login - 校园网自动登录（Srun / 锐捷 ePortal / 通用网页表单）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  campusnet                     连接一次（首次会启动设置向导）\n"
            "  campusnet --setup             重新设置门户、账号和密码\n"
            "  campusnet --status            查看在线状态\n"
            "  campusnet --watch             掉线自动重连\n"
            "  campusnet --detect            只探测登录门户，不发送账号密码\n"
            "  campusnet --diagnose          输出脱敏诊断信息（适合发 issue）\n"
            "  campusnet --forget            删除本机保存的密码\n"
            "  campusnet --stop              停止后台守护进程\n"
        ),
    )
    parser.add_argument("--watch", action="store_true", help="守护模式，掉线自动重连")
    parser.add_argument("--status", action="store_true", help="只查看在线状态")
    parser.add_argument("--setup", action="store_true", help="启动设置向导")
    parser.add_argument("--detect", action="store_true", help="探测登录门户（不发送密码）")
    parser.add_argument("--diagnose", action="store_true", help="输出脱敏诊断信息")
    parser.add_argument("--forget", action="store_true", help="删除本机保存的密码")
    parser.add_argument("--stop", action="store_true", help="停止后台守护进程")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="指定配置文件路径（默认自动查找）",
    )
    parser.add_argument(
        "--portal",
        metavar="URL",
        help="临时指定门户登录页（用于测试，不写入配置）",
    )
    parser.add_argument(
        "--protocol",
        choices=PROTOCOL_CHOICES,
        default="auto",
        help="临时指定协议（auto/srun/ruijie/generic）",
    )
    parser.add_argument(
        "--ip",
        metavar="IP",
        help="手动指定本机在校园网中的 IPv4（一般不需要）",
    )
    parser.add_argument("--quiet", action="store_true", help="不输出到控制台，只写日志")
    parser.add_argument("--verbose", action="store_true", help="输出调试信息")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _prepare_config(
    args: argparse.Namespace,
) -> tuple[Path, Config, bool]:
    path = resolve_config_path(args.config)
    created = ensure_config(path)
    cfg = load_config(path)

    if args.portal:
        cfg.url = args.portal.strip()
        # --portal 是临时调试入口；没有显式 --protocol 时按 auto 重新识别。
        cfg.protocol = args.protocol if args.protocol != "auto" else "auto"
    elif args.protocol != "auto":
        cfg.protocol = args.protocol

    return path, cfg, created


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.stop:
        logger = _plain_logger(quiet=args.quiet)
        if not watch_is_running():
            if consume_stop_request():
                pass
            logger.info("当前没有正在运行的守护进程。")
            return 0
        if not request_stop():
            logger.error("无法写入停止标记。")
            return 1
        logger.info("已通知守护进程停止（最多等待一个检查周期）。")
        return 0

    if args.forget:
        logger = _plain_logger(quiet=args.quiet)
        path = resolve_config_path(args.config)
        username = ""
        if path.exists():
            try:
                username = load_config(path).username
            except Exception:
                pass
        store = CredentialStore()
        removed = store.delete(username) if store.available or IS_WINDOWS else False
        if removed:
            logger.info("已删除本机保存的密码。")
        else:
            logger.info("本机没有保存过密码。")
        if path.exists():
            try:
                if load_config(path).password:
                    logger.warning(
                        "注意：config.ini 中仍然写有明文密码，请自行清空。"
                    )
            except Exception:
                pass
        return 0

    try:
        cfg_path, cfg, _created = _prepare_config(args)
    except (ConfigError, OSError) as exc:
        safe_print(f"配置错误：{exc}")
        return 1

    logger = setup_logging(cfg, quiet=args.quiet, verbose=args.verbose)
    http = _make_http(cfg)
    store = CredentialStore()

    if args.detect:
        return run_detect(cfg, http, logger, args.portal)
    if args.diagnose:
        return run_diagnose(cfg, cfg_path, http, logger, ip_override=args.ip)

    if args.setup:
        code = wizard.run_setup_wizard(
            cfg,
            cfg_path,
            http,
            logger,
            store,
            protocol_preference=args.protocol,
        )
        return code

    configured = cfg.is_configured()

    if args.status:
        if not configured:
            logger.error("尚未配置门户。请先运行：python campus_net.py --setup")
            return 2
        return run_status(cfg, http, logger, ip_override=args.ip)

    if not configured:
        if not is_interactive():
            logger.error("尚未配置门户。请先双击“一键联网.bat”完成首次设置。")
            show_message(
                "CampusNet",
                "还没有完成校园网设置。\n\n"
                "请双击“一键联网.bat”，按提示输入登录页、账号和密码。",
            )
            return 2
        safe_print("尚未配置校园网门户，正在启动设置向导……")
        code = wizard.run_setup_wizard(
            cfg,
            cfg_path,
            http,
            logger,
            store,
            protocol_preference=args.protocol,
        )
        if code != 0:
            return code
        cfg = load_config(cfg_path)
        if not args.watch:
            return 0
        http = _make_http(cfg)

    if args.watch:
        return run_watch(
            cfg, http, logger, store, quiet=args.quiet, ip_override=args.ip
        )

    return connect_once(cfg, http, logger, store, ip_override=args.ip)
