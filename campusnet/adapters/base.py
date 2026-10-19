# -*- coding: utf-8 -*-
"""协议适配器接口。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..config import Config
from ..http import HttpClient
from ..models import Detection, LoginResult, PageProbe, PortalContext


@dataclass
class Status:
    online: Optional[bool]  # True / False / None(无法判断)
    info: Optional[dict] = None
    message: str = ""


class ProtocolAdapter(ABC):
    protocol = ""
    display_name = ""

    def __init__(
        self,
        cfg: Config,
        http: HttpClient,
        logger: logging.Logger,
        ip_override: Optional[str] = None,
    ):
        self.cfg = cfg
        self.http = http
        self.logger = logger
        self.ip_override = ip_override

    @classmethod
    @abstractmethod
    def detect(cls, page: PageProbe) -> Detection:
        """页面指纹匹配，score=0 表示不匹配。"""

    @classmethod
    @abstractmethod
    def context_from_page(
        cls, cfg: Config, page: PageProbe
    ) -> PortalContext:
        """从已探测到的登录页构建上下文（不应发送认证信息）。"""

    @abstractmethod
    def prepare(self, context: PortalContext) -> PortalContext:
        """补齐登录所需的参数（IP、ac_id、queryString 等）。"""

    @abstractmethod
    def status(self, context: PortalContext) -> Status:
        """查询在线状态。"""

    @abstractmethod
    def login(
        self, context: PortalContext, username: str, password: str
    ) -> LoginResult:
        ...

    def verify(
        self, context: PortalContext, username: str, tries: int = 3
    ) -> Optional[dict]:
        """登录后确认。返回在线信息，None 表示暂时无法确认。"""
        return None

    # 通用联网检测：非 Srun 协议用它判断是否已经可以上网。
    def internet_status(self) -> Status:
        value = self.http.check_internet(self.cfg.connectivity_urls)
        if value is True:
            return Status(True, {"connectivity_only": True})
        if value is False:
            return Status(False, None)
        return Status(None, None, "无法判断联网状态")
