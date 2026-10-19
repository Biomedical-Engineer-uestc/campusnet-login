#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：保留原来的 campus_net.py 调用方式。

实际实现位于 campusnet/ 包中。
"""

from campusnet.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
