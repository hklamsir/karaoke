# -*- coding: utf-8 -*-
"""progress — 分階段進度元件（計劃書 §2.3 B4 / §4.5.4）。

五個階段 chip（抽音/上傳/ASR/斷句/渲染）+ 進度條（上傳階段 indeterminate）
+ 當前階段說明 + 可展開 log 窗。thread-safe：背景 thread 經 ``report()`` 投遞，
UI ``after()`` poll queue。
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk

import ttkbootstrap as ttkb

from .. import theme

#: generate 五個階段（同 main.generate_timings progress_cb stage 名一致）
STAGES = ["抽音", "上傳", "ASR", "斷句", "渲染"]


class QueueLogHandler(logging.Handler):
    """把 logger 記錄導入 StageProgress log 窗（thread-safe，經 queue）。"""

    def __init__(self, progress: "StageProgress") -> None:
        super().__init__()
        self._progress = progress

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._progress.log(self.format(record))
        except Exception:   # noqa: BLE001 — logging handler 唔准再拋
            pass

_STATE_ICON = {"pending": "○", "active": "◐", "done": "●", "error": "✕"}
_STATE_STYLE = {"pending": "secondary", "active": "info", "done": "success", "error": "danger"}


class StageProgress(ttkb.Frame):
    """分階段進度條 + log 窗（背景 thread 友好）。"""

    def __init__(self, master, stages: list[str] | None = None, *,
                 log_open: bool = False, log_height: int = 8) -> None:
        """Args:
            master: 父容器。
            stages: 階段名清單（預設 generate 五階段）。
            log_open: log 窗預設是否展開（燒錄對話框用；用戶回報要預設見到 log）。
            log_height: log 文字區高度（行數）。
        """
        super().__init__(master, bootstyle="light")
        self._stages = stages or STAGES
        self._queue: queue.Queue = queue.Queue()
        self._states: dict[str, str] = {s: "pending" for s in self._stages}
        self._chips: dict[str, ttkb.Label] = {}
        #: 外部已用 set_fraction() 喂真實進度 → 唔好再被 report() 打回跑馬燈
        self._determinate = False

        chips_row = ttkb.Frame(self, bootstyle="light")
        chips_row.pack(fill="x")
        for stage in self._stages:
            chip = ttkb.Label(chips_row, text=f"○ {stage}", padding=(8, 3),
                              bootstyle="secondary", relief="flat")
            chip.pack(side="left", padx=2)
            self._chips[stage] = chip

        self._bar = ttkb.Progressbar(self, mode="determinate", maximum=100, value=0)
        self._bar.pack(fill="x", pady=(6, 2))
        self._msg_var = tk.StringVar(value="待命")
        ttkb.Label(self, textvariable=self._msg_var, bootstyle="secondary",
                   font=(theme.ui_font_family(), 9)).pack(anchor="w")

        # 可展開 log 窗（log_open=True 時建立即展開）
        self._log_open = False
        self._toggle_btn = ttkb.Button(self, text="▾ log", width=6, bootstyle="secondary",
                                       command=self._toggle_log, takefocus=False)
        self._toggle_btn.pack(anchor="e", pady=(2, 0))
        self._log = tk.Text(self, height=log_height, state="disabled", wrap="word",
                            bg=theme.BG_DEEP, fg=theme.FG_DIM,
                            font=(theme.mono_font_family(), 9), relief="flat",
                            highlightthickness=1, highlightbackground=theme.BORDER)
        if log_open:
            self._toggle_log()

        self.after(80, self._poll)

    # ------------------------------------------------------------------
    # 背景 thread 呼叫（thread-safe，只准 queue.put）
    # ------------------------------------------------------------------

    def report(self, stage: str, message: str) -> None:
        """階段回呼：stage 名要喺 STAGES 內；message 為空代表階段完成。"""
        self._queue.put((stage, message))

    def log(self, text: str) -> None:
        self._queue.put(("", "LOG:" + text))

    def set_fraction(self, pct: float, message: str | None = None) -> None:
        """餵入真實百分比（0–100），把進度條由跑馬燈切成確定模式（ADR-028）。

        背景 thread 可直接呼叫。之後同階段的 ``report()`` 唔會再打回
        indeterminate —— 直到 ``reset()`` / ``succeed()``。

        Args:
            pct: 0–100；超出範圍會 clamp。
            message: 同時更新嘅說明文字；``None`` = 唔改。
        """
        self._queue.put(("__pct__", f"{pct}|{message or ''}"))

    def reset(self) -> None:
        self._queue.put(("__reset__", ""))

    def fail(self, message: str) -> None:
        self._queue.put(("__fail__", message))

    def succeed(self, message: str = "完成 ✅") -> None:
        self._queue.put(("__done__", message))

    # ------------------------------------------------------------------
    # UI thread
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                stage, message = self._queue.get_nowait()
                self._apply(stage, message)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _apply(self, stage: str, message: str) -> None:
        if stage == "__reset__":
            self._determinate = False
            self._states = {s: "pending" for s in self._stages}
            self._refresh_chips()
            self._bar.stop()
            self._bar.configure(mode="determinate", value=0)
            self._msg_var.set("待命")
            return
        if stage == "__pct__":
            raw, _sep, msg = message.partition("|")
            try:
                pct = float(raw)
            except ValueError:
                return
            self._determinate = True
            self._bar.stop()
            self._bar.configure(mode="determinate", maximum=100,
                                value=max(0.0, min(100.0, pct)))
            if msg:
                self._msg_var.set(msg)
            return
        if stage == "__fail__":
            self._determinate = False
            if self._current_stage():
                self._states[self._current_stage()] = "error"
            self._refresh_chips()
            self._bar.stop()
            self._bar.configure(mode="determinate")
            self._msg_var.set(message)
            return
        if stage == "__done__":
            self._determinate = False
            for s in self._states:
                if self._states[s] in ("pending", "active"):
                    self._states[s] = "done"
            self._refresh_chips()
            self._bar.stop()
            self._bar.configure(mode="determinate", value=100)
            self._msg_var.set(message)
            return
        if message.startswith("LOG:"):
            self._append_log(message[4:].rstrip("\n"))
            return

        if stage in self._states:
            self._states[stage] = "done" if not message else "active"
            self._refresh_chips()
            self._msg_var.set(f"{stage}：{message}" if message else f"{stage} 完成")
            # 上傳階段時長波動大（3 秒~3 分鐘）→ 未有真實百分比前用跑馬燈
            # （計劃書 §5.2）；set_fraction() 餵過數就一路保持確定模式。
            if message and self._states[stage] == "active":
                if not self._determinate:
                    self._bar.configure(mode="indeterminate")
                    self._bar.start(24)
            else:
                self._determinate = False
                self._bar.stop()
                self._bar.configure(mode="determinate")
                self._advance_determinate(stage)

    def _advance_determinate(self, done_stage: str) -> None:
        idx = self._stages.index(done_stage)
        self._bar.configure(value=100 * (idx + 1) / (len(self._stages) + 0.5))

    def _current_stage(self) -> str:
        for s in self._stages:
            if self._states[s] == "active":
                return s
        return ""

    def _refresh_chips(self) -> None:
        for stage, state in self._states.items():
            chip = self._chips[stage]
            chip.configure(text=f"{_STATE_ICON[state]} {stage}", bootstyle=_STATE_STYLE[state])

    def _toggle_log(self) -> None:
        self._log_open = not self._log_open
        if self._log_open:
            self._log.pack(fill="both", expand=True, pady=(4, 0))
            self._toggle_btn.configure(text="▴ log")
        else:
            self._log.pack_forget()
            self._toggle_btn.configure(text="▾ log")

    def _append_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        # 限制行數防長 MV log 爆記憶體
        if int(self._log.index("end-1c").split(".")[0]) > 2000:
            self._log.delete("1.0", "200.0")
        self._log.see("end")
        self._log.xview_moveto(0)   # 長行（ffmpeg 指令）唔好令短行都被推到左邊切走
        self._log.configure(state="disabled")
