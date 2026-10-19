# -*- coding: utf-8 -*-
"""config.ini 读写、默认模板和旧配置迁移。"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from typing import Optional

from .errors import ConfigError
from .models import DEFAULT_CONNECTIVITY_URLS, Config

CONFIG_HEADER = """\
# CampusNet Login 配置文件
# 首次运行会自动检测门户；也可以手动修改下面的 [portal] 部分。
# 不要把有明文密码的 config.ini 提交到公开仓库！
"""

DEFAULT_CONFIG_TEMPLATE = (
    CONFIG_HEADER
    + """
[account]
# 校园网账号 / 学号
username =

# 密码：推荐留空。留空时首次运行会提示输入，并用系统安全存储保存。
# 只有在明确知道风险时才在这里写明文密码。
password =

# 是否保存手动输入的密码
remember_password = true

[portal]
# auto = 自动识别；也可手动指定 srun / ruijie / generic
protocol = auto

# 首次向导确认后的门户登录页地址。留空则首次运行时自动检测。
url =

# Srun 可选备用地址，逗号分隔
hosts =
# Srun 认证参数；留空时从登录页读取
ac_id =
theme = pro

# 锐捷 ePortal 的服务名（学校不同可能不同，留空则从页面读取）
service =

# 通用表单/锐捷：auto = 自动判断密码是否要 MD5/Base64；也可强制 plain/md5/sha1/base64
password_encoding = auto

# 通用表单可选覆盖项
username_field =
password_field =
login_url =
# 额外表单字段，格式：key=value; key2=value2
extra_fields =
# 自定义成功关键词，逗号分隔
success_keywords =

# 联网检测地址，逗号分隔
connectivity_urls = {connectivity}

timeout = 8
verify_tls = true
# 通用表单提交到不同域名时默认拒绝，避免把密码发到意外地址
allow_cross_origin = false

[watch]
interval = 30
log = true
""".format(connectivity=", ".join(DEFAULT_CONNECTIVITY_URLS))
)


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]


def _split_hosts(value: str) -> list[str]:
    hosts = []
    for item in _split_list(value):
        item = item.rstrip("/")
        if not item.startswith(("http://", "https://")):
            item = "http://" + item
        hosts.append(item)
    return hosts


def _parse_extra_fields(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in re.split(r"[;\n]", value or ""):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, val = item.split("=", 1)
        key = key.strip()
        if key:
            result[key] = val.strip()
    return result


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "y")


def config_exists(path: Path) -> bool:
    return path.exists()


def ensure_config(path: Path) -> bool:
    """不存在时写默认模板。返回是否新建。"""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return True


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except Exception as exc:
        raise ConfigError(f"读取配置文件失败：{exc}") from exc

    def get(section: str, key: str, default: str = "") -> str:
        if parser.has_option(section, key):
            return parser.get(section, key, raw=True)
        return default

    username = get("account", "username").strip()
    password = get("account", "password")
    remember = _bool(get("account", "remember_password", "true"), True)

    raw_hosts = get("portal", "hosts")
    hosts = _split_hosts(raw_hosts)

    protocol = get("portal", "protocol").strip().lower() or ""
    url = get("portal", "url").strip()

    # 兼容 v1：只有 hosts，没有 protocol/url。
    if not protocol:
        protocol = "srun" if hosts else "auto"
    if not url or url.lower() == "auto":
        url = hosts[0] if hosts else ""
    if protocol not in ("auto", "srun", "ruijie", "generic"):
        raise ConfigError(f"不支持的 protocol：{protocol}")

    try:
        timeout = float(get("portal", "timeout", "8").strip() or "8")
    except ValueError:
        timeout = 8.0
    timeout = min(30.0, max(3.0, timeout))

    try:
        interval = int(get("watch", "interval", "30").strip() or "30")
    except ValueError:
        interval = 30
    interval = min(3600, max(2, interval))

    raw_connectivity = get("portal", "connectivity_urls").strip()
    connectivity_urls = (
        _split_list(raw_connectivity)
        if raw_connectivity
        else list(DEFAULT_CONNECTIVITY_URLS)
    )

    raw_keywords = get("portal", "success_keywords").strip()
    success_keywords = _split_list(raw_keywords) if raw_keywords else []

    return Config(
        username=username,
        password=password,
        remember_password=remember,
        protocol=protocol,
        url=url,
        hosts=hosts,
        ac_id=get("portal", "ac_id").strip(),
        theme=get("portal", "theme").strip() or "pro",
        service=get("portal", "service").strip(),
        password_encoding=get("portal", "password_encoding").strip().lower()
        or "auto",
        username_field=get("portal", "username_field").strip(),
        password_field=get("portal", "password_field").strip(),
        login_url=get("portal", "login_url").strip(),
        extra_fields=_parse_extra_fields(get("portal", "extra_fields")),
        success_keywords=success_keywords,
        connectivity_urls=connectivity_urls,
        timeout=timeout,
        verify_tls=_bool(get("portal", "verify_tls", "true"), True),
        allow_cross_origin=_bool(get("portal", "allow_cross_origin", "false"), False),
        interval=interval,
        log_enabled=_bool(get("watch", "log", "true"), True),
    )


def save_config(path: Path, cfg: Config) -> None:
    """原子写入配置。密码字段默认保持为空。"""
    parser = configparser.ConfigParser(interpolation=None)
    parser["account"] = {
        "username": cfg.username,
        "password": cfg.password or "",
        "remember_password": str(bool(cfg.remember_password)).lower(),
    }
    parser["portal"] = {
        "protocol": cfg.protocol,
        "url": cfg.url,
        "hosts": ", ".join(cfg.hosts),
        "ac_id": cfg.ac_id,
        "theme": cfg.theme,
        "service": cfg.service,
        "password_encoding": cfg.password_encoding,
        "username_field": cfg.username_field,
        "password_field": cfg.password_field,
        "login_url": cfg.login_url,
        "extra_fields": "; ".join(f"{k}={v}" for k, v in cfg.extra_fields.items()),
        "success_keywords": ", ".join(cfg.success_keywords),
        "connectivity_urls": ", ".join(cfg.connectivity_urls),
        "timeout": f"{cfg.timeout:g}",
        "verify_tls": str(bool(cfg.verify_tls)).lower(),
        "allow_cross_origin": str(bool(cfg.allow_cross_origin)).lower(),
    }
    parser["watch"] = {
        "interval": str(cfg.interval),
        "log": str(bool(cfg.log_enabled)).lower(),
    }

    from io import StringIO

    buf = StringIO()
    parser.write(buf)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(CONFIG_HEADER + "\n" + buf.getvalue(), encoding="utf-8")
    os.replace(tmp, path)
