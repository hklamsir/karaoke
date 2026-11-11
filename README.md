# 卡拉OK字幕產生器（Karaoke ASS Generator）

透過阿里百煉雲 `qwen-audio-3.1-asr-flash-filetrans` 辨識 MV 中的歌詞與時間軸，
產出 `*_timings.json`（結構化時間軸，canonical）與支援平滑逐字變色的 `*_karaoke.ass`，
再以 ffmpeg 將字幕燒錄成 mp4。

本專案提供兩個入口，共用同一條處理管線與同一份 `*_timings.json`，可以交替使用：

| | 圖形介面（GUI） | 命令列（CLI） |
|---|---|---|
| 啟動方式 | 雙擊 `run_ui.bat` | `python main.py <命令>` |
| 適用場景 | 選片、校對時間軸、微調樣式、燒錄 | 批次處理、自動化、整合至其他腳本 |
| 涵蓋範圍 | 生成 → 校對 → 燒錄 完整流程 | `generate` / `render` / `burn` 三條命令 |
| 依賴環境 | `.venv-ui` + `requirements-ui.txt` | `.venv` + `requirements.txt`（刻意保持輕量） |
| 需要 tkinter | 是 | 否 |

> 📐 術語定義見 [`docs/名詞表.md`](docs/名詞表.md)；難以回頭的技術決定見
> [`docs/架構決策紀錄.md`](docs/架構決策紀錄.md)。**修改程式碼前請先閱讀這兩份文件。**
>
> **適用範圍**（ADR-012）：粵語／國語／英語的**書面語**歌。錯字預期由字幕編輯介面人工修正。
>
> 📦 **下載最新版（Windows 交付包）**：<https://github.com/hklamsir/karaoke/releases/latest>
> 安裝步驟見 [`DEPLOY.md`](DEPLOY.md)（四步：Python → FFmpeg → 解壓 → 雙擊 `run_ui.bat`）。

---

## 1 · 安裝

### 1.1 FFmpeg（必須）

| 系統 | 安裝方式 |
|---|---|
| Windows | `winget install Gyan.FFmpeg`，或由 <https://ffmpeg.org> 下載後加入 `PATH` |
| macOS | `brew install ffmpeg` |
| Linux | `sudo apt install ffmpeg` |

`generate` 需要 ffmpeg 抽取音訊；`burn` 需要 ffmpeg 與 ffprobe；`render` 兩者皆不需要。

### 1.2 Python 依賴

需要 Python 3.10 或以上。兩個入口使用各自獨立的虛擬環境：

| 入口 | 安裝指令 |
|---|---|
| **CLI** | `python -m venv .venv`<br>`.venv\Scripts\activate`（macOS/Linux：`source .venv/bin/activate`）<br>`pip install -r requirements.txt` |
| **GUI** | `py -3 -m venv .venv-ui`<br>`.venv-ui\Scripts\python.exe -m pip install -r requirements-ui.txt`<br>`.venv-ui\Scripts\python.exe -m ui` |

CLI 依賴刻意保持輕量：`dashscope` / `requests` / `python-dotenv` / `opencc-python-reimplemented`。
GUI 另需 `ttkbootstrap`（介面主題）／`numpy`（波形峰值）／`pygame`（音訊播放與游標同步），
可選安裝 `tkinterdnd2` 以支援拖放檔案。

> ⚠️ **兩個環境不可互換**：`.venv` 不含 tkinter，直接以它啟動介面會失敗（`ui/__main__.py` 會攔截並提示）。
> 不想手動建立環境，可直接雙擊 `run_ui.bat`：它會自動建立 `.venv-ui`、依 `requirements.lock.txt`
> 安裝固定版本依賴、檢查 tkinter 與 ffmpeg，然後開啟視窗。首次約需下載 200 MB（3–5 分鐘），
> 之後再開會跳過安裝（約 10 秒）。

### 1.3 API Key

三種方式擇一，優先序為 `--api-key` > 環境變數 `DASHSCOPE_API_KEY` > `.env` > `~/.karaoke/settings.json`：

```bash
export DASHSCOPE_API_KEY="sk-xxxx"   # Windows PowerShell: $env:DASHSCOPE_API_KEY="sk-xxxx"
# 或寫入 .env 檔：DASHSCOPE_API_KEY=sk-xxxx
# 或於命令列指定：--api-key "sk-xxxx"
```

圖形介面的設定頁亦可填寫，但**不會覆蓋已設定的環境變數**。

### 1.4 人聲分離（可選，僅影響 ASR 音源）

```bash
pip install audio-separator     # 刻意不列入 requirements.txt（會引入 GB 級依賴，ADR-010）
```

首次執行會下載模型。此選項**只影響送去辨識的音源**，不會改變燒錄出來的音軌。

> ⚠️ **燒錄不會替你移除人聲**（ADR-020）。曾經內建 AI 去人聲，但實測細節流失明顯
> （歌聲殘留、樂器變薄）、依賴達 1.3 GB、CPU 上慢 18 倍，已整套移除。
> 需要純音樂版本，請**自備去人聲的 MV**，並在燒錄時選用它（見 §4）。

---

## 2 · 圖形介面（GUI）

### 2.1 啟動

雙擊 `run_ui.bat`，或於已建立的 `.venv-ui` 環境執行 `python -m ui`。
視窗標題會顯示版本號（例如 `Karaoke AI Subtitler - Designed by Lamsir  (v1.0.2)`）。

介面採 ttkbootstrap，內建三套主題，於設定頁即時切換：

| 主題 | 說明 |
|---|---|
| `litera` | 淺色 · 白底黑字（預設） |
| `darkly` | 深色 · 炭灰 |
| `superhero` | 深色 · 深藍橙 |

### 2.2 四個頁面

以 `Alt+1` 至 `Alt+4` 切換。

**主頁** — 選擇 MV 後，以 ffprobe 讀取解析度與時長。屬性分三層（基本／樣式／進階），
按「▶ 開始生成」後分五階段顯示進度（抽取音訊 → 上傳 → 語音辨識 → 斷句 → 渲染）。
上傳階段會顯示**真實位元組百分比**（ADR-028）；若自建上傳被代理中斷，會自動退回
SDK 路線（該階段退回跑馬燈，不會中斷流程）。進度框固定在右欄底部，不會被視窗裁走。

- **樣式**：字型、字級、已唱色／未唱色／描邊色（色塊與十六進位值雙向同步）、描邊粗細、對齊、底部留白（滑桿＋精確 px 數字框，0 = 自動）。
  **主頁樣式為燒錄的權威來源**（ADR-015）：燒錄時一律套用此處設定並寫回 `*_timings.json`；開啟舊檔時會自動載入該檔樣式。
- **提前顯示 / 倒數圓點**（ADR-031）：長間奏期間**唔會再提早幾十秒**出下一句 ——
  每句最多提早「提前顯示」設定的毫秒（預設 3000）出現；由空白段進入歌詞前的 1.5 秒，
  **歌詞第一個字上方**會逐點亮起 3 個圓點（每 0.5 秒一點，全用唱過色），開唱一刻消失。
  圓點只在「上一句結束後至少隔 5 秒」（可調，0 = 停用）的長空白才出，密的句子唔會出。
  設「提前顯示」為 0 即完全回到舊行為（輸出 byte-identical）。
  「換行延遲」在提前顯示開啟時 = **句尾停留**（最後一字後多留幾耐才清空，預設 1000ms）。
- **雙行歌詞**（ADR-027 / ADR-030）：勾選後畫面固定兩行 —— 奇數句在上、偶數句在下，
  每句在「下一句開唱 + 換行延遲（預設 1000ms）」時被同行的再下一句取代。
  佈局同常見卡拉OK 原片一致（上行顯示正在唱、下行預覽下一句）。行距自動取字級的 1.45 倍，
  字級細（例如預設 48px）時兩行會較貼，1080p 建議 90–110px、底部留白 100–120px。
  - **對齊**：雙行會跟「對齊」設定 —— 頂部置中由畫面頂往下排、中部置中圍繞中心、
    底部置中貼底（之前雙行被硬編碼成底部，係 bug，已修）。
  - **上行位置 / 下行位置**：可各自選「偏左 / 居中 / 偏右」（±10% 畫面闊度），
    做出原片「上偏左、下偏右」的錯位效果。
  - **字型**：下拉會列出系統全部字型，**中文字型排前**；只會列出實際存在的字型，
    避免 libass 靜默 fallback。原片那種明體（`PMingLiU`）需自行安裝，否則可選
    `SimSun` / `FangSong` / `Microsoft JhengHei UI`。
- **進階**（預設收合）：保留標點、每行字數上限、停頓門檻、短句門檻、合併字數上限、句尾停留、人聲分離、儲存原始 JSON、辨識模型。
- **即時預覽**：任何樣式變更立即重繪（以系統字型渲染，並非 libass 逐像素輸出，與最終成品可能有細微差異）。
- 亦可按「開啟現有 `*_timings.json`」直接進入校對，無須重跑辨識。

**編輯器** — 左側歌詞樹（一句一行，橙色表示該行含估算時間的字，雙擊可跳至該句），
左下逐字微調（每個字的開始與結束時間以時間碼 `3:41.995` 呈現，時間欄有 ▲▼ 可 ±10ms
即時微調，另可多選、± 平移、
跨字均分，亦可一鍵修補 AI 漏字造成的時長崩壞），右上預覽窗（疊加字幕、當前字幕卡、2 fps 縮圖捲軸），
右下時間軸（波形、逐字方塊、播放頭、全片概覽條）。
預覽工具列可選**解析度**（跟原片 / 1920×1080 / 1280×720 / 854×480 / 自訂）與**縮放**
（適合視窗 / 50% / 100%，ADR-032）—— 揀「跟原片 + 100%」即係用輸出影片的真實解析度、
1:1 檢視字幕實際大小；超出窗格會自動降到適合視窗並在狀態列顯示實際百分比。
`Ctrl+S` 儲存（原子寫入並保留 `.bak`）。

**設定** — DashScope API Key、FFmpeg／FFprobe 路徑（預設自動偵測）、預設資料夾、
介面主題、縮圖快取容量與清除。

**說明** — 即 [`docs/使用說明.md`](docs/使用說明.md) 的內容，左側章節可跳轉，
上方搜尋框支援全文搜尋；「複製等價 CLI 指令」會把目前參數組成一行可執行的命令。

### 2.3 時間軸操作

| 操作 | 行為 |
|---|---|
| 滾輪 | 以游標位置為中心縮放（四級：0.25× / 1× / 4× / 16×） |
| `Shift` + 滾輪 | 水平捲動 |
| 拖曳底部藍色概覽條 | 水平平移 |
| 左鍵點擊空白處 | 定位游標並播放 |
| 拖曳字塊本體 | 整體平移該詞的所有字 |
| 拖曳字塊左右邊界 | 精調該字的開始或結束時間 |

### 2.4 快捷鍵

| 快捷鍵 | 作用 |
|---|---|
| `Alt+1` ～ `Alt+4` | 切換頁面（主頁／編輯器／設定／說明） |
| `Ctrl+S` | 儲存 `timings.json`（原子寫入並保留 `.bak`） |
| `Ctrl+Z` / `Ctrl+Shift+Z` | 復原／重做 |
| `空白鍵` | 播放／暫停 |
| `←` / `→` | 游標移動 ±100 ms |
| `Shift+←` / `Shift+→` | 游標移動 ±10 ms（精調） |
| `Ctrl+←` / `Ctrl+→` | 游標移動 ±1 幀（60 fps 約 17 ms） |
| `Ctrl+O` | 開啟現有 `timings.json`（主頁） |

> 燒錄流程見 §4。

---

## 3 · 命令列（CLI）

### 3.1 三條命令

```
generate   影片 → ASR → timings.json + ass      （預設不輸出影片）
render     timings.json → ass                   （無須重跑 ASR）
burn       影片 + ass → mp4                     （只接受既有的 ass）
```

`.timings.json` 為 canonical source of truth；`.ass` 是由它渲染出來的衍生物。

```
┌─ generate ─────────────────────────────────────────────┐
│  原始 MV → 16k mono TempAudio → 上傳 → ASR 異步轉寫      │
│         → 拆字(WordGroup→CharSlot) → OpenCC s2hk        │
│         → 斷句 → *_timings.json ──► *_karaoke.ass       │
└────────────────────────────────────────────────────────┘
        │                                   │
        │  （GUI / 人工修改 timings.json）     │
        ▼                                   ▼
   render *.json  ──► *.ass            burn 影片+ass ──► *_karaoke.mp4
```

⚠️ **`burn` 永遠不會隱含 `render`**，不會靜默覆寫你手動修改過的 `.ass`（ADR-012）。

### 3.2 `generate` — 生成字幕

```bash
# 最簡用法：產出 MV_timings.json 與 MV_karaoke.ass
python main.py generate -i "C:\Videos\MV.mp4"

# 指定語言與輸出資料夾
python main.py generate -i "MV.mp4" -l yue -o "./subs"

# 指定字級與底部留白（避開 MV 內嵌字幕）
python main.py generate -i "MV.mp4" --font-size 72 --margin-v 160
```

實測 180 秒 MV 約需 **11 秒**（辨識本身 4–7 秒，上傳耗時 3 秒至 3 分鐘不等）。
`-l yue` 內部會重映射為 `language_hints=['zh']`，原因見 ADR-001。

### 3.3 `render` — 重新產生 `.ass`

校對過時間或文字後執行此命令即可，無須重跑辨識：

```bash
python main.py render MV_timings.json

# 本次輸出套用新顏色（不寫回 json）
python main.py render MV_timings.json --primary-color "#00FFFF" --font-size 64

# --sync-style：把覆寫值一併寫入 timings.json（持久）
python main.py render MV_timings.json --primary-color "#00FFFF" --sync-style

# 換了另一版剪輯的 MV（前奏長短唔同）→ 整體延遲 250ms
python main.py render MV_timings.json --shift-ms 250
```

`--shift-ms` 只影響輸出 `.ass`，**不會改動 `timings.json` 的原始時間**（ADR-029）。

同一份 json 連續渲染兩次，輸出為 **byte-identical**；未加 `--sync-style` 時不會改動輸入檔。

### 3.4 `burn` — 燒錄成影片

```bash
python main.py burn -i "MV.mp4" -s "MV_karaoke.ass"
```

預設輸出 `<原檔名>_karaoke.mp4`，編碼為 libx264 crf18 medium，音軌 `-c:a copy`
（不相容時降級為 aac 192k）。更換音源或自備純音樂 MV 的做法見 §4。

### 3.5 常用參數

| 參數 | 用途 |
|---|---|
| `--separate-vocals` | 先做人聲分離再送辨識（背景音樂強烈時使用） |
| `--max-chars 18` | 每行字數上限。**ADR-013 後屬安全網**：斷行以標點優先，僅超長分句才兜底切分 |
| `--min-clause-chars 5` | 短句門檻字數（≤5 字**且**時長 <2500 ms 才與鄰句合併） |
| `--min-clause-ms 2500` | 短句門檻時長；拖長音的短句仍獨立成行，保留強調效果 |
| `--merge-max-chars 16` | 合併後每行字數上限（兜底切行仍用 `--max-chars`） |
| `--gap-threshold 800` | 兜底斷句的停頓閾值（ms），僅於超長分句內生效 |
| `--keep-punctuation` | 保留標點。預設移除：行尾刪除，行中轉為半形空格加 `{\k<gap>}` 停頓 |
| `--hold-ms 300` | 句尾額外停留時間（ms） |
| `--margin-v` | 底部垂直留白（px），`0` 表示按 PlayResY 自動（1080p 約 59 px） |
| `--lead-in-ms 3000` | 歌詞提前顯示上限（ms）。長間奏唔會再提早幾十秒出下一句；`0` = 關閉 |
| `--no-countdown-dots` | 關閉「進入歌詞前 3 個倒數圓點」（預設開啟） |
| `--dot-min-gap-ms 5000` | 圓點門檻：上一句結束後至少隔幾多 ms 才出圓點（0 = 有空白就出） |
| `--save-raw` | 額外儲存辨識原始 JSON，方便除錯 |
| `-v` | 輸出 DEBUG 層級日誌（置於子命令前後皆可） |

完整參數表以 `python main.py <命令> -h` 查詢，或見 [`docs/使用說明.md`](docs/使用說明.md) §4
（該表由 argparse 動態產生，不會與程式碼脫節）。

---

## 4 · 燒錄與自備純音樂 MV

GUI 與 CLI 共用同一套規則（ADR-020）：本工具**不會**替你移除人聲，但允許你換用自備的伴奏版本。

**圖形介面**：於編輯器按「🔥 燒錄」→ 燒錄設定。預設使用原片；
按「選擇純音樂 MV…」可換成自備的去人聲版本，畫面與音軌皆取自該檔，字幕燒在其上。
確認後會先依主頁樣式重新產生 `.ass`，再交由 ffmpeg 燒錄。
燒錄期間會顯示 ffmpeg 的**實際編碼進度**（`已編碼 m:ss / 總長 m:ss（xx%）`，ADR-028；
讀 `-progress` 臨時檔，不佔 pipe，取消功能照常運作）。

**命令列**：把 `-i` 指向伴奏版本，`.ass` 仍用原片那份：

```bash
python main.py burn -i "MV_純音樂.mp4" -s "MV_karaoke.ass" -o "MV_karaoke_inst.mp4"
```

燒錄設定對話框會做三項**燒錄前預警**（僅提示，不會自動修正）：

| 檢查 | 觸發條件 | 後果 |
|---|---|---|
| 時長差異 | 與原片相差 > 1.5 秒 | 字幕會逐漸偏移（多為不同剪輯版本） |
| 畫面比例 | 比例相差 > 5%（例如 16:9 對 1:1） | 字幕位置可能需要重新調整 |
| 無視訊軌 | 選到 mp3 / wav | 直接停用「開始燒錄」 |

> 自備 MV 必須是**同一個剪輯版本**（前奏與尾奏長度一致）。字幕時間軸由原片的辨識結果而來，
> 換版本就必須回到編輯器手動調整；軟件不會亦不可能自動對齊。

---

## 5 · `timings.json` 結構

```jsonc
{
  "schema_version": 3,
  "source": { "video": "...", "model": "...", "language_hints": ["zh"],
              "min_clause_chars": 5, "min_clause_ms": 2500, "strip_punctuation": true,
              "generated_at": "..." },            // UI 儲存時另加 edited_at / edited_by
  "style":  { "play_res_x": 1920, "play_res_y": 1080, "font_name": "Microsoft JhengHei",
              "font_size": 48, "primary_color": "#FFFF00", "secondary_color": "#FFFFFF",
              "outline_color": "#000000", "alignment": 2, "margin_v": 0, "outline_width": 0.0,
              "lead_in_ms": 3000, "countdown_dots": true,
              "dot_min_gap_ms": 5000 },                     // ADR-031（非預設值才寫）
  "lines": [{
    "start_ms": 3950, "end_ms": 8890,
    "text": "輪迴塵間的人 揮之不能",        // ADR-013：顯示用文字（已移除標點）
    "break_reason": "punctuation",          // punctuation / sentence / max_chars / gap / manual
    "words": [{
      "text": "輪迴", "begin_ms": 3950, "end_ms": 4670,
      "estimated": true,
      "punctuation": "",                    // ADR-013：此詞之後的標點（CharSlot 永遠是純字）
      "chars": [ { "text": "輪", "begin_ms": 3950, "end_ms": 4310 },
                 { "text": "迴", "begin_ms": 4310, "end_ms": 4670 } ]
    }]
  }]
}
```

**四項必讀**

1. **`estimated: true`** 是介面的核心欄位：表示該詞內部的逐字時間軸由程式按字數比例估算，
   並非辨識結果實際提供。實測一首 MV 有 **40% 的詞／57% 的字**屬此類，必須標示予使用者人工校對。
2. **全部為絕對毫秒**（`begin_ms` / `end_ms` / `line.start_ms` / `line.end_ms`），
   修改一個字不會影響其他字。`render` 直接使用儲存值，**不會重新推算** `hold_ms`，也不會自動裁剪至下一句。
3. **兩層結構**：拖動 `words[]` 可整詞平移，拖動 `chars[]` 可調整單字。
   純拉丁詞（例如 `hello`）為 `estimated=false` 且只有一個 char（刻意不拆分，否則英文會逐字母閃動）。
4. **標點與斷句（ADR-013）**：顯示標點請讀 `words[].punctuation`；`lines[].text` 是衍生物。
   schema v3 起，介面手動斷句會寫入 `break_reason: "manual"`。

**版本相容**：v1 舊檔（標點黏在 `text` 尾）與 v2 舊檔讀入時會自動相容，僅記錄警告。

**標準寫入流程**

```
1. 讀取 *_timings.json
2. 調整 words[] / chars[] 的 begin_ms / end_ms（或直接改 line.start_ms / end_ms）
3. 寫回 *_timings.json
4. python main.py render <json>          ← 必須先執行這步
5. python main.py burn -i MV.mp4 -s <ass>
```

⚠️ **請勿直接修改 `.ass`**：下次 `render` 會無條件覆寫它。所有改動都應寫入 json。

---

## 6 · 已知陷阱

| 陷阱 | 後果 | 對策 |
|---|---|---|
| ASS 時間戳寫成 `M:SS:cc` | ffmpeg 不報錯，輸出**完全沒有字幕**的影片 | 一律使用 `ms_to_ass_time()`（ADR-005） |
| 以 ffmpeg 絕對路徑作 `ass=` | Windows 的 `C:` 冒號被當成 filter 分隔符而失敗 | 切換 `cwd` 並只用檔名（ADR-006） |
| 未帶 `original_size` | 字幕比例走位 | 帶 `original_size=WxH`，PlayRes 由 ffprobe 讀取原片 |
| `language_hints=['yue']` | 粵語被「翻譯」成普通話書面語 | 內部重映射為 `zh`（ADR-001） |
| 使用 `paraformer-v2` | 「100% 逐字」實為線性插值的假時間軸 | 永久停用（ADR-004） |
| 以 `split(",,")` 取 ASS 的 Text 欄 | 會誤取 Margin 欄的 `0,0,0,` | 改用 `split(",", 9)[9]` |
| 直接手動修改 `.ass` | 下次 `render` 會被覆寫 | 修改 `timings.json` 後重新 `render` |

---

## 7 · 疑難排解

```
[error] 找不到 ffmpeg。
```
→ 見 §1.1。安裝後請**重新開啟終端機**，令 `PATH` 生效。

```
[error] 找不到 DashScope API Key。
```
→ 見 §1.3。

```
[error] ASR 任務失敗：code=... message=...
```
→ 加 `--save-raw` 保留原始 JSON 以便檢查；常見原因是 key 的區域不符或模型名稱錯誤。

```
[error] timings.json 的 style.primary_color 有問題：...
```
→ json 被手動改壞。`render` 會退回預設值，但顏色格式必須為 6 位十六進位。

```
No module named 'tkinter'
```
→ 以 `.venv` 啟動了介面。請改用 `.venv-ui` 或雙擊 `run_ui.bat`（見 §1.2）。

---

## 8 · 已知限制

以林家謙《普渡眾生》MV（1920×1080 / AV1 / 180 秒）完成端到端驗證（2026-10-29）：

**✅ 已確認可行**

- `generate` 約 **11 秒**完成（上傳時間波動大，實測 3 秒至 3 分鐘）
- 同一份 json 執行 `render` 兩次，輸出 **byte-identical**（無 round-trip 漂移）
- `burn` 由 `.ass` 讀取座標系，輸出 57 MB mp4，保留 AAC 44.1 kHz 立體聲原音軌
- `\kf` 逐字漸層變色、底部居中、黑色描邊，均已目視確認

**⚠️ 限制**

- **非確定性**：同一段音訊每次辨識結果都有差異（例如 14 句與 12 句、逗號位置不同），字幕質素會有波動。
- **歌詞大片空缺**：該 MV 全長 180 秒，`asr_content_duration_ms` 僅約 119 秒；純音樂段落本來就沒有歌詞。
- **單字雜訊**：辨識偶爾在片尾吐出單字殘句（實測 `"用。"`），`generate` 已自動丟棄少於 2 個有效字的句子。
- **沒有 LLM 歌詞校正**（ADR-012 刻意移除）：錯字需在介面人工修正。書面語歌的辨識準確度約 **89.7%**。
- 口語比重高或咬字含糊的歌曲**尚未驗證**（本工具定位為書面語歌）。

---

## 9 · 專案結構與版本

```
main.py               CLI 入口與處理管線（generate / render / burn）
ui/                   圖形介面（app.py 主視窗、pages/ 各頁、theme.py 色票）
tools/                build_release.py（出包）、version_guard.py（版本護欄）
tests/                pytest 測試（見下）
  fixtures/           sample_v3.json（合成 timings doc）、builder.py（大量資料生成器）、
                      media.py（用 ffmpeg lavfi 即場生成測試片，唔入版控）
docs/                 使用說明、名詞表、架構決策紀錄
VERSION               版本號唯一真源
requirements.txt      CLI 依賴（開發用寬鬆範圍）
requirements.lock.txt 交付用鎖定版本
requirements-dev.txt  測試依賴（pytest）
pytest.ini            pytest 設定（testpaths / slow 標記）
run_ui.bat            GUI 啟動器（自檢 → 建 venv → 安裝 → 啟動）
run_tests.bat         一鍵跑測試（自動驗收入口）
.github/workflows     tests.yml（push 自動跑 pytest）
```

### 跑測試

第一次先裝測試依賴（只需一次）：

```bat
.venv-ui\Scripts\python.exe -m pip install -r requirements-dev.txt
```

然後：

```bat
run_tests.bat                 全部（含 3 小時 / 5,000 行的大數據量測試、真實 ffmpeg 燒錄）
run_tests.bat -m "not slow"   快速回歸（約 10 秒）
```

判準為 **exit code 0**。現時 **213 條全綠、0 xfail** —— 審查報告 §9.2 的必補案例
（取消、靜音偵測、跨磁碟燒錄、原子寫入、UI 防呆、長片記憶體）已全部實作並鎖住。

`xfail(strict=True)` 的約定仍然保留：新增功能時先寫契約測試，未實作會 xfail
（suite 保持綠），實作完成後自動 XPASS 並令 CI 變紅，強制拆除標記 ——
即「實作即驗收」。`pytest -rx` 可列出全部待實作項目。


版本號以根目錄 `VERSION` 為唯一真源，三處顯示：啟動器 banner、視窗標題、`python main.py --version`。
出包與部署流程見 [`DEPLOY.md`](DEPLOY.md)。
