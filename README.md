# 卡拉OK字幕產生器（Karaoke ASS Generator）

把本地 MV 短片透過**阿里百煉雲 `qwen-audio-3.1-asr-flash-filetrans`** 自動識別歌詞與時間軸，
產出 `*_timings.json`（結構化時間軸，**canonical**）同支援**平滑逐字變色**的 `*_karaoke.ass`。

> 📐 本專案所有術語見 [`docs/名詞表.md`](docs/名詞表.md)，所有難以回頭的決定見
> [`docs/架構決策紀錄.md`](docs/架構決策紀錄.md)。**改 code 前請先讀呢兩份。**
>
> **範圍**（ADR-012）：粵語 / 國語 / 英語的**書面語**歌。錯字預期由字幕編輯 UI 人手修正。

---

## 1. 三條命令

```
generate   影片 → ASR → timings.json + ass      （預設唔出影片）
render     timings.json → ass                   （毋須重跑 ASR）
burn       影片 + ass → mp4                     （淨係食 ass）
```

`.timings.json` 係 canonical source of truth；`.ass` 係由佢渲染出嚟的衍生物。

```
┌─ generate ─────────────────────────────────────────────┐
│  原始 MV → 16k mono TempAudio → 上傳 → ASR 異步轉寫      │
│         → 拆字(WordGroup→CharSlot) → OpenCC s2hk        │
│         → 斷句 → *_timings.json ──► *_karaoke.ass       │
└────────────────────────────────────────────────────────┘
        │                                   │
        │  （UI / 人手改 timings.json）        │
        ▼                                   ▼
   render *.json  ──► *.ass            burn 影片+ass ──► *_karaoke.mp4
```

⚠️ **`burn` 永遠唔會隱含 render**，唔會靜默洗走你手改過嘅 ass（ADR-012）。

---

## 2. 安裝

### 2.1 系統層級：FFmpeg（**必須**）

| OS | 安裝方式 |
|---|---|
| Windows | `winget install Gyan.FFmpeg`，或由 <https://ffmpeg.org> 下載並加落 `PATH` |
| macOS | `brew install ffmpeg` |
| Linux | `sudo apt install ffmpeg` |

> `generate` 需要 ffmpeg 抽音；`burn` 需要 ffmpeg + ffprobe（`render` 兩樣都唔使）。

### 2.2 Python 依賴

建議 Python 3.10+。

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

刻意保持輕量：`dashscope` / `requests` / `python-dotenv` / `opencc-python-reimplemented`。

### 2.3 API Key

三種方式任一（優先序：`--api-key` > 環境變數 `DASHSCOPE_API_KEY` > `.env`）：

```bash
export DASHSCOPE_API_KEY="sk-xxxx"   # Windows PowerShell: $env:DASHSCOPE_API_KEY="sk-xxxx"
# 或放 .env 檔： DASHSCOPE_API_KEY=sk-xxxx
# 或用 --api-key "sk-xxxx"
```

### 2.4 可選：`generate` 的人聲分離（只影響 ASR 音源）

```bash
pip install audio-separator     # 刻意唔入 requirements.txt（會拖入 GB 級依賴，ADR-010）
```

首次執行會下載模型。**只影響送去做 ASR 的音源**，唔會改變燒錄出來的音軌。

> ⚠️ **燒錄唔會幫你去人聲**（ADR-020）。曾經內建過 AI 去人聲，但實測細節流失明顯
> （歌聲殘留、樂器變薄）、依賴 1.3 GB、CPU 上慢 18 倍，已整套移除。
> 要出純音樂，請**自備去人聲的純音樂 MV**，燒錄時喺對話框揀它（見 3.4）。

---

## 3. 使用方法

### 範例 1 · 生成字幕（`timings.json` + `ass`，**唔出影片**）

```bash
python main.py generate -i "C:\Videos\MV.mp4"
```

產出 `MV_timings.json` + `MV_karaoke.ass`。實測 180 秒 MV 只需 **約 11 秒**。

指定粵語（`-l yue` 內部會重映射成 `language_hints=['zh']`，見 ADR-001）：

```bash
python main.py generate -i "MV.mp4" -l yue -o "./subs"
```

### 範例 2 · 改完時間軸後重新渲染（**唔使再跑 ASR**）

```bash
python main.py render MV_timings.json
```

改樣式而唔想重跑 ASR：

```bash
# 本次 render 生效
python main.py render MV_timings.json --primary-color "#00FFFF" --font-size 64

# 連 timings.json 入面嘅 style 都一併改（持久）
python main.py render MV_timings.json --primary-color "#00FFFF" --sync-style
```

### 範例 3 · 燒錄成影片

```bash
# 用原片（保留原聲）
python main.py burn -i "MV.mp4" -s "MV_karaoke.ass"

# 用自備的純音樂 MV（去人聲版本）—— 字幕時間軸仍然來自原片那份 timings.json
python main.py burn -i "MV_純音樂.mp4" -s "MV_karaoke.ass"
```

預設輸出 `<原檔名>_karaoke.mp4`，libx264 crf18 medium、音軌 `-c:a copy`（唔相容則降級 aac 192k）。

#### 圖形介面：燒錄時揀音源

按「🔥 燒錄」會彈**燒錄設定**，預設用原片；按「選擇純音樂 MV…」可換成你自備的去人聲版本
—— 畫面與音軌都會取自該檔，字幕燒在它上面。

對話框會做兩項**燒錄前預警**（唔會自動修，只會提你）：

| 檢查 | 觸發條件 | 後果 |
|---|---|---|
| 時長差 | 與原片相差 > 1.5 秒 | 字幕會愈唱愈唔對位（唔同剪輯版本） |
| 畫面比例 | 比例相差 > 5%（例如 16:9 vs 1:1） | 字幕位置可能要重新調整 |
| 冇視訊軌 | 揀到 mp3 / wav | 直接禁用「開始燒錄」 |

> 自備 MV 一定要係**同一個剪輯版本**（前奏/尾奏長度一致）。字幕時間軸是從原片 ASR 出來的，
> 換了版本就必須返編輯器手動調時間軸 —— 軟件唔會（亦唔可能）自動對齊。

### 其他常用參數（`generate`）

| 參數 | 用途 |
|---|---|
| `--separate-vocals` | 先做人聲分離再送 ASR（背景音樂強烈時用） |
| `--max-chars 16` | 每行最多字數，預設 18。**ADR-013 後係安全網**：斷行以**標點優先**（句讀 + sentence 邊界），淨係分句超長先兜底 |
| `--gap-threshold 800` | 兜底斷句嘅停頓閾值（ms），淨喺超長分句內生效 |
| `--min-clause-chars 5` | **ADR-013**：短句門檻字數（≤5 字 **且** 時長 <2500ms 先同鄰句合併） |
| `--min-clause-ms 2500` | **ADR-013**：短句門檻時長（ms）；拖長音嘅短句照出一行，保留強調效果 |
| `--merge-max-chars 16` | **ADR-013 修訂**：合併後嘅行不可超過嘅字數（兜底切行仍然用 `--max-chars`） |
| `--keep-punctuation` | **ADR-013**：字幕保留標點。預設**剝走**：行尾標點刪、行中標點變半形空格 + `{\k<gap>}` 停頓 |
| `--hold-ms 300` | 句尾多留幾多 ms 先換行 |
| `--margin-v 160` | 底部垂直留白（px），`0` = 按 PlayResY 自動（1080p ≈ 59px）。**MV 自帶內嵌字幕時調大避開佢**；`render` 都有（覆寫用） |
| `--save-raw` | 額外存 ASR 原始 JSON 方便除錯 |
| `-v` | DEBUG 層級 log（`main.py -v generate ...` 或 `... generate ... -v` 都得） |

---

## 4. `timings.json` 結構（做 UI 請睇呢節）

```jsonc
{
  "schema_version": 2,
  "source": { "video": "...", "model": "...", "language_hints": ["zh"],
              "min_clause_chars": 3, "min_clause_ms": 1500, "strip_punctuation": true,
              "generated_at": "..." },
  "style":  { "play_res_x": 1920, "play_res_y": 1080, "font_name": "Microsoft JhengHei",
              "font_size": 48, "primary_color": "#FFFF00", "secondary_color": "#FFFFFF",
              "outline_color": "#000000", "alignment": 2, "margin_v": 0, "outline_width": 0.0 },
  "lines": [{
    "start_ms": 3950, "end_ms": 8890,
    "text": "輪迴塵間的人 揮之不能",        // ADR-013：顯示用文字（已剝標點、行中標點變空格）
    "break_reason": "punctuation",          // ADR-013：punctuation / sentence / max_chars / gap
    "words": [{
      "text": "輪迴", "begin_ms": 3950, "end_ms": 4670,
      "estimated": true,
      "punctuation": "",                    // ADR-013：呢個詞後面嘅標點（CharSlot 永遠純字）
      "chars": [ { "text": "輪", "begin_ms": 3950, "end_ms": 4310 },
                 { "text": "迴", "begin_ms": 4310, "end_ms": 4670 } ]
    }]
  }]
}
```

> ⚠️ **ADR-013**：`words[].text` 同 `chars[].text` 由 v2 起係**純字**（唔含標點）；
> 標點獨立放 `words[].punctuation`。讀入 v1 舊檔會自動相容（標點拆返出嚟，只 warn）。

**UI 必讀三點**

1. **`estimated: true`** —— 呢個字段係 UI 嘅核心。表示該詞**內部嘅逐字時間軸係由程式按比例平分估出嚟**，
   唔係 ASR 真實畀嘅。實測一首 MV 有 **40% 嘅詞 / 57% 嘅字**屬呢類，必須標示畀用戶知要人手校。
2. **全部係絕對 ms**（`begin_ms` / `end_ms` / `line.start_ms` / `line.end_ms`），改一個字唔會影響其他字。
   `render` 直接用儲存值，**唔會重新推算** `hold_ms` 或幫你 clip 到下一句。
3. **兩層結構**：拖 `words[]` 改成個詞、拖 `chars[]` 改單字都支援。
   純拉丁詞（`hello`）`estimated=false` 且只有一個 char（刻意唔拆，否則英文會逐字母閃）。
4. **標點（ADR-013）**：UI 想顯示標點就讀 `words[].punctuation`；想改斷行就改
   `words[].punctuation` / `lines[].break_reason` 對應嘅位置。`lines[].text` 係衍生物，
   改完 word 記得同步（或者信 render 時嘅 `display_text()`）。

### UI 嘅標準寫入流程

```
1. 讀 *_timings.json
2. 用戶拖動 words[] / chars[] 嘅 begin_ms / end_ms（或直接改 line.start_ms / end_ms）
3. 寫返落 *_timings.json
4. 叫 python main.py render <json>      ← 一定行呢步先
5. 叫 python main.py burn -i MV.mp4 -s <ass>
```

⚠️ **唔好直接寫 .ass**：下次 `render` 會無條件覆寫佢。所有改動落地去 json。

---

## 5. 已知陷阱（踩過，唔好再踩）

| 陷阱 | 後果 | 對策 |
|---|---|---|
| ASS 時間戳寫 `M:SS:cc` | ffmpeg 唔報錯，輸出**完全冇字幕**的影片 | 一律用 helper `ms_to_ass_time()`（ADR-005） |
| ffmpeg 絕對路徑做 `ass=` | Windows `C:` 冒號被當 filter 分隔符而爆 | 切 `cwd` + 只用檔名（ADR-006） |
| 唔帶 `original_size` | 字幕比例走位 | 帶 `original_size=WxH`，且 PlayRes 由 ffprobe 讀原片 |
| `language_hints=['yue']` | 粵語被「翻譯」成普通話書面語 | 內部重映射 `zh`（ADR-001） |
| 用 `paraformer-v2` | 「100% 逐字」其實係線性插值假時間軸 | 永久禁用（ADR-004） |
| ASS Dialogue 取 Text 用 `split(",,")` | 會錯誤撈到 Margin 欄嘅 `0,0,0,` | 用 `split(",", 9)[9]`（本篇評估腳本踩過） |
| 直接手改 `.ass` | 下次 `render` 會被覆寫 | 改 `timings.json` 再 `render` |

---

## 6. 疑難排解

```
[error] 搵唔到 ffmpeg。
```
→ 見 2.1，安裝後**重開終端機**令 PATH 生效。

```
[error] 搵唔到 DashScope API Key。
```
→ 見 2.3。

```
[error] ASR 任務失敗：code=... message=...
```
→ 用 `--save-raw` 存 JSON 檢查；常見係 key region 唔啱或 model 名錯。

```
[error] timings.json 的 style.primary_color 有問題：...
```
→ json 被人手改壞。`render` 會兜底用預設值，但顏色格式必須係 6 位 hex。

---

## 7. 已知限制（2026-10-29 真實 MV 實測）

已用林家謙《普渡眾生》MV（1920×1080 / AV1 / 180 秒）做完端到端驗證：

**✅ 已確認可行**
- `generate` 約 **11 秒**完成（ASR 本身只用 4~7 秒；上傳時間波動大，實測 3s ~ 3min）
- `render` 同一份 json 行兩次 → **byte-identical**（冇 round-trip 漂移）
- `render` 唔會改動輸入 json（除非 `--sync-style`）
- `burn` 由 `.ass` 讀到座標系，出 57MB mp4、保留 AAC 44.1kHz 立體聲原音軌
- `\kf` 逐字漸層變色（`PrimaryColour` = 唱過色）、底部居中、黑色描邊全部目視確認

**⚠️ 限制**
- **非確定性**：ASR 每次跑嘅結果都有差異（14 句 vs 12 句、句數同逗號位置都唔同）。字幕質素有波動。
- **歌詞大片空缺**：該 MV 全長 180 秒，`asr_content_duration_ms` 只係 ~119 秒，純音樂段落本來就冇字幕。
- **單字雜訊**：ASR 有時喺片尾吐出單字殘句（實測 `"用。"`）→ `generate` 已自動丟棄少於 2 個有效字嘅句子。
- **冇 LLM 歌詞校正**（ADR-012 刻意移除）：錯字要喺 UI 人手改。書面語歌的 ASR 準確度約 **89.7%**。
- 口語比重高 / 咬字含糊嘅歌**未驗證**（本工具定位係書面語歌）。

## 8. GUI 使用指南

字幕編輯器 UI 已經內置（`ui/`，ttkbootstrap 深色主題）：

```bash
py -3 -m venv .venv-ui
.venv-ui/Scripts/python.exe -m pip install -r requirements-ui.txt
.venv-ui/Scripts/python.exe -m ui
```

- **主頁**：揀 MV → 揀屬性（三層：基本/樣式/進階）→ 開始生成（五階段進度）；或直接「開啟現有 `*_timings.json`」
- **編輯器**：歌詞樹 + 波形時間軸 + 逐字微調 + 大 preview 窗（字幕疊圖）+ 2fps 縮圖 scrub strip；估算字橙色標示、可篩選、可重新劃分；Ctrl+S 儲存（atomic + .bak）
- **燒錄**：編輯器工具列「🔥 燒錄」→ 燒錄設定（可換成自備純音樂 MV）→ 自動 render 最新 .ass + ffmpeg
- **設定**：API key（env 優先）/ ffmpeg 路徑 / 預設資料夾 / 主題
- **幫助**：UI + CLI 完整使用方法（CLI 參數表動態生成，永不過時）；內容源 `docs/使用說明.md`

> CLI 三命令（generate / render / burn）行為不變；UI 透過 `import main` 呼叫同一條 pipeline。
