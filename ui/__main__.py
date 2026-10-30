# -*- coding: utf-8 -*-
"""``python -m ui`` 啟動入口。"""

from __future__ import annotations

import sys

from .app import run


if __name__ == "__main__":
    sys.exit(run())
