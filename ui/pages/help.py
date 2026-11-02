# -*- coding: utf-8 -*-
"""pages.help — 幫助頁（計劃書 §5.5；版面依據 docs/線框圖/04_幫助頁.html）。

內容源：``docs/使用說明.md``（markdown 子集 → Text 渲染）+ ``clidoc.py`` 動態 CLI 表。
文件唔存在時 fallback 顯示內置說明（R18）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as messagebox
from pathlib import Path

import ttkbootstrap as ttkb

from .. import theme
from ..clidoc import collect, generate_cli_command
from ..widgets.markdown_view import MarkdownView

DOC_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "使用說明.md"

_FALLBACK = """\
# 1 · 快速上手
文件未安裝（搵唔到 docs/使用說明.md）。流程：主頁揀片 → 揀屬性 → 開始生成 → 編輯器校時間軸 → 燒錄。
"""


def _insert_clidoc_tables(markdown: str) -> str:
    """將 ``<!-- CLIDOC:command -->`` 佔位符替換成動態參數表（markdown pipe table）。"""
    out: list[str] = []
    for line in markdown.splitlines():
        out.append(line)
        stripped = line.strip()
        if stripped.startswith("<!-- CLIDOC:") and stripped.endswith("-->"):
            command = stripped[len("<!-- CLIDOC:"):-len("-->")]
            rows = collect().get(command, [])
            out.append("| 旗標 | 預設 | 說明 |")
            out.append("|---|---|---|")
            for p in rows:
                help_text = p.help.replace("|", "／")
                out.append(f"| `{p.flags}` | {p.default_text()} | {help_text} |")
            out.append("")
    return "\n".join(out)


class HelpPage(ttkb.Frame):
    """幫助：章節錨點 + 搜尋 + markdown 內容區 + 複製等價 CLI 指令。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="light")

        top = ttkb.Frame(self, bootstyle="light")
        top.pack(fill="x", pady=(0, theme.SPACE))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        ttkb.Label(top, text="搜尋", bootstyle="secondary").pack(side="left",
                                                              padx=(0, 6))
        # 白底主題下 Entry 會同頁面融為一體 → 專屬 style 用面板色 + 邊框
        self._style_widgets()
        ttkb.Entry(top, textvariable=self._search_var, width=32,
                   style="Search.TEntry",
                   font=(theme.ui_font_family(), 10)).pack(side="left")
        ttkb.Button(top, text="📋 複製等價 CLI 指令", bootstyle="secondary",
                    takefocus=False, command=self._copy_cli).pack(side="left", padx=theme.SPACE)
        ttkb.Button(top, text="開啟原文", bootstyle="secondary", takefocus=False,
                    command=self._open_source).pack(side="left")

        anchors = ttkb.Frame(self, bootstyle="light", width=210)
        anchors.pack(side="left", fill="y", padx=(0, theme.SPACE))
        anchors.pack_propagate(False)
        # 用戶需求：章節字級要同內容一致 —— 內容 base 係 12pt，所以標題與樹項都用 12pt
        ttkb.Label(anchors, text="章節", bootstyle="secondary",
                   font=(theme.ui_font_family(), 12, "bold")).pack(anchor="w", pady=(0, 4))
        self._anchor_tree = ttkb.Treeview(anchors, show="tree", selectmode="browse",
                                          style="Anchor.Treeview")
        self._anchor_tree.pack(fill="both", expand=True)
        self._anchor_tree.bind("<<TreeviewSelect>>", self._on_anchor_select)

        self._view = MarkdownView(self)
        vsb = ttkb.Scrollbar(self, orient="vertical", command=self._view.yview,
                             bootstyle="secondary-round")
        self._view.configure(yscrollcommand=vsb.set)
        self._view.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self._search_job = None
        self._load_content()

    # ------------------------------------------------------------------

    def _style_widgets(self) -> None:
        """設定自訂 style（搜尋框 / 章節樹）—— 換主題時要重跑，所以抽成方法。

        Treeview 字級要經 style（ttk 冇 ``-font``）；``Anchor.Treeview`` 只係
        喺 ``Treeview`` 之上加字級 + 面板底色，選取色跟色票 accent。
        """
        st = ttkb.Style()
        st.configure("Search.TEntry", fieldbackground=theme.BG_PANEL,
                     bordercolor=theme.BORDER, lightcolor=theme.BG_PANEL,
                     darkcolor=theme.BG_PANEL, insertcolor=theme.FG_MAIN,
                     foreground=theme.FG_MAIN)
        st.configure("Anchor.Treeview", font=(theme.ui_font_family(), 12),
                     rowheight=30, background=theme.BG_PANEL,
                     fieldbackground=theme.BG_PANEL,
                     foreground=theme.FG_MAIN, bordercolor=theme.BORDER)
        st.map("Anchor.Treeview",
               background=[("selected", theme.ACCENT)],
               foreground=[("selected", theme.ON_ACCENT)])

    def restyle(self) -> None:
        """換主題：重設自訂 style 與搜尋命中色（Text tag 係 per-widget）。"""
        self._style_widgets()
        self._view.tag_configure("search_hit", background=theme.SEARCH_HIT,
                                 foreground=theme.FG_MAIN)

    def _load_content(self) -> None:
        if DOC_PATH.is_file():
            markdown = DOC_PATH.read_text(encoding="utf-8")
            self._doc_missing = False
        else:
            markdown = _FALLBACK      # R18：文件未安裝 fallback
            self._doc_missing = True
        self._markdown = _insert_clidoc_tables(markdown)
        self._view.delete("1.0", "end")
        self._view.render(self._markdown)
        if self._doc_missing:
            self._view.render("\n⚠ 文件未安裝：搵唔到 docs/使用說明.md，顯示內置說明。\n")
        self._build_anchors()
        self._show_section(1)

    def _build_anchors(self) -> None:
        """由內容區 h1 行號建章節錨點樹。"""
        self._anchor_tree.delete(*self._anchor_tree.get_children())
        self._sections: list[tuple[str, int]] = []     # (title, line_no)
        for i, line in enumerate(self._view.get("1.0", "end").splitlines(), start=1):
            tag_names = self._view.tag_names(f"{i}.2")
            # 用 tag 掃：h1 行 = 有 h1 tag 嘅行
        # Text 迭代 tag 行（tag_ranges 更可靠）
        self._sections.clear()
        ranges = self._view.tag_ranges("h1")
        for k in range(0, len(ranges), 2):
            start = str(ranges[k])
            text = self._view.get(start, f"{start} lineend").strip()
            self._sections.append((text, int(start.split(".")[0])))
        for i, (title, _line) in enumerate(self._sections):
            self._anchor_tree.insert("", "end", iid=str(i), text=title, open=False)

    def _on_anchor_select(self, _event=None) -> None:
        sel = self._anchor_tree.selection()
        if sel:
            self._show_section(int(sel[0]))

    def _show_section(self, idx: int) -> None:
        if not (0 <= idx < len(self._sections)):
            return
        _title, line = self._sections[idx]
        self._view.see(f"{max(1, line - 1)}.0")

    # ------------------------------------------------------------------
    # 搜尋（§5.5.6：250ms debounce）
    # ------------------------------------------------------------------

    def _on_search_changed(self, *_args) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, self._do_search)

    def _do_search(self) -> None:
        query = self._search_var.get().strip()
        self._view.tag_remove("search_hit", "1.0", "end")
        self._view.tag_configure("search_hit", background=theme.SEARCH_HIT,
                                 foreground=theme.FG_MAIN)
        if not query:
            return
        start = "1.0"
        count = tk.IntVar()
        first_hit = None
        while True:
            start = self._view.search(query, start, stopindex="end",
                                      count=count, nocase=True)
            if not start:
                break
            self._view.tag_add("search_hit", start, f"{start}+{len(query)}c")
            if first_hit is None:
                first_hit = start
            start = f"{start}+{count.get()}c"
        if first_hit:
            self._view.see(first_hit)

    # ------------------------------------------------------------------

    def _copy_cli(self) -> None:
        """複製一份等價 CLI 指令示範（用 generate 預設 + 常用覆寫例子）。"""
        cmd = generate_cli_command("generate", {
            "input": "C:\\Videos\\MV.mp4",
            "font_size": 72,
            "primary_color": "#0000FF",
            "margin_v": 160,
            "keep_punctuation": True,
        })
        self.clipboard_clear()
        self.clipboard_append(cmd)
        messagebox.showinfo("已複製", f"已複製到剪貼簿：\n\n{cmd}", parent=self)

    def _open_source(self) -> None:
        import webbrowser
        if DOC_PATH.is_file():
            webbrowser.open(DOC_PATH.as_uri())
        else:
            messagebox.showwarning("文件未安裝", "搵唔到 docs/使用說明.md。", parent=self)
