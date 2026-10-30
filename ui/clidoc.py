# -*- coding: utf-8 -*-
"""clidoc — 由 argparse **動態**生成 CLI 參數表 + 等價 CLI 指令（計劃書 §5.5.3 / §5.5.5）。

⭐ CLI 參數表唔寫死：讀 ``main.build_parser()`` 嘅 ``_actions``，``main.py`` 加新參數，
幫助頁自動跟住更新（V12 驗證：同 ``main.py --help`` 100% 一致）。
"""

from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # import main


@dataclass
class ParamInfo:
    """一個 CLI 參數（argparse action 嘅 UI 友好摘要）。"""

    flags: str                 # "-l, --language"
    dest: str
    default: Any
    help: str
    required: bool = False
    choices: list | None = None

    def default_text(self) -> str:
        if self.required:
            return "（必填）"
        if self.default is None:
            return "—"
        if isinstance(self.default, bool):
            return "開" if self.default else "關"
        if isinstance(self.default, str) and self.default.startswith("_"):
            return "—"
        return str(self.default)


def _describe(action: argparse.Action) -> ParamInfo | None:
    if action.dest in ("help", "func"):
        return None
    flags = ", ".join(action.option_strings) or action.dest
    return ParamInfo(
        flags=flags, dest=action.dest, default=action.default,
        help=(action.help or "").replace("%(default)s", str(action.default)),
        required=bool(getattr(action, "required", False)),
        choices=list(action.choices) if action.choices else None,
    )


def collect() -> dict[str, list[ParamInfo]]:
    """{'generate': [...], 'render': [...], 'burn': [...]}（唔包頂層 -v）。"""
    import main
    parser = main.build_parser()
    out: dict[str, list[ParamInfo]] = {}
    for action in parser._actions:      # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
            for name, sub in action.choices.items():
                params = [p for p in (_describe(a) for a in sub._actions)  # noqa: SLF001
                          if p is not None]
                out[name] = params
    return out


def flag_of(command: str, dest: str) -> str:
    """dest → flag 字串（R17：argparse 冇反查 API，自己 build）。"""
    for param in collect().get(command, []):
        if param.dest == dest:
            return param.flags.split(",")[0].strip() or f"--{param.dest}"
    return f"--{dest}"


def generate_cli_command(command: str, values: dict[str, Any]) -> str:
    """由 {dest: value} 生成等價 CLI 指令（V13：貼落終端機實跑要一致）。

    淨係輸出**同預設唔同**嘅參數；路徑用 shlex quote（Windows path 冇空格都包，
    穩陣）。store_true 嘅 False 就唔出。
    """
    import main
    parser = main.build_parser()
    defaults: dict[str, Any] = {}
    for action in parser._actions:      # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
            if command in action.choices:
                defaults = {a.dest: a.default for a in action.choices[command]._actions}  # noqa: SLF001
    parts = [f"python main.py {command}"]
    for dest, value in values.items():
        if dest in ("verbose", "func", "command") or value is None:
            continue
        if value == defaults.get(dest):
            continue
        flag = flag_of(command, dest)
        if isinstance(value, bool):
            if value:
                parts.append(flag)
        else:
            parts.append(f"{flag} {shlex.quote(str(value))}")
    return " \\\n  ".join(parts)
