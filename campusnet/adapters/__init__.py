# -*- coding: utf-8 -*-
"""协议适配器注册表。"""

from __future__ import annotations

from typing import Optional

from ..models import Detection, PageProbe
from .base import ProtocolAdapter, Status
from .generic import GenericAdapter
from .ruijie import RuijieAdapter
from .srun import SrunAdapter

ADAPTERS = {
    SrunAdapter.protocol: SrunAdapter,
    RuijieAdapter.protocol: RuijieAdapter,
    GenericAdapter.protocol: GenericAdapter,
}

PROTOCOL_CHOICES = ("auto", "srun", "ruijie", "generic")


def detect_protocols(page: PageProbe) -> list[Detection]:
    results = []
    for adapter in ADAPTERS.values():
        detection = adapter.detect(page)
        if detection.score > 0:
            results.append(detection)
    results.sort(key=lambda item: item.score, reverse=True)
    return results


def best_detection(page: PageProbe) -> Optional[Detection]:
    results = detect_protocols(page)
    if not results:
        return None
    best = results[0]
    # 通用表单的分数天然低于专门协议；只要有表单就可以作为兜底。
    if best.protocol == "generic" and best.score >= 0.1:
        return best
    if best.score >= 0.5:
        return best
    return None


def adapter_class(protocol: str):
    return ADAPTERS.get(protocol)


__all__ = [
    "ADAPTERS",
    "PROTOCOL_CHOICES",
    "ProtocolAdapter",
    "Status",
    "detect_protocols",
    "best_detection",
    "adapter_class",
]
