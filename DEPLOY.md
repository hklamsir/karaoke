# DEPLOY — 部署到第二台電腦（Windows）

本文件寫給**接手的人**（同事），目標是：拿到一個 zip，30 分鐘內在自己的 Windows 電腦上跑起字幕編輯器，並燒出一條有字幕的 mp4。

> 本工具分兩個入口，共用同一條管線與同一份 `*_timings.json`：
> - 圖形介面：雙擊 `run_ui.bat`
> - 命令列：`python main.py generate|render|burn`
>
> 完整功能說明見 `docs/使用說明.md`（UI 內「幫助」頁就是讀這份）；術語見 `docs/名詞表.md`。

---

## 0 · 前置條件

| 項目 | 要求 | 為什麼 |
|---|---|---|
| 作業系統 | Windows 10 / 11（64-bit） | 啟動器是 `.bat`，圖示是 `.ico`；macOS / Linux 未驗證 |
| Python | **3.13**（3.10 以上可試，未驗證） | 本工具在 3.13.15 上實測；`run_ui.bat` 用 `py -3` 啟動器 |
| FFmpeg | 需要 `ffmpeg` 與 `ffprobe` 兩支 | 抽音、讀解析度、燒錄都靠它 |
| 網絡 | 首次安裝需要（下載約 200MB 套件）；執行時需要（呼叫阿里百煉 ASR） | — |
| DashScope API Key | 自己申請一把，**不要用別人的** | 按用量計費（ASR 一般按音頻時長），額度與帳單算在自己頭上 |
| 磁碟 | ≥ 1GB（venv 約 320MB + 快取） | — |

> ⚠️ **解壓位置**：建議 `C:\Karaoke`。
> **不要**放在 OneDrive / 桌面 / 文件 這類會同步的資料夾 —— venv 有幾萬個小檔案，同步會拖慢整台機，而且可能鎖住檔案令 pip 安裝失敗。
> 路徑**不要含中文或空格**（`C:\我的程式\卡拉OK` 這種出事機會不高但沒必要冒險）。

---

## 1 · 安裝（四步）

### 1.1 裝 Python

```powershell
winget install -e --id Python.Python.3.13
```

裝完**關掉終端機再開新的**（PATH 要重讀），然後驗證：

```powershell
py -0
```

應該見到 `-V:3.13 *` 這一行，星號代表預設版本。

> 已經有 Python 3.10–3.13 任一版本可以跳過此步；但**必須有 `py` 啟動器**（`py -0` 有輸出）。沒有的話，改用官方安裝包並勾選「py launcher」。

### 1.2 裝 FFmpeg

```powershell
winget install -e --id Gyan.FFmpeg
```

裝完同樣**重開終端機**，驗證兩支都要有：

```powershell
ffmpeg -version
ffprobe -version
```

兩個都印出版本號才算成功。

> 若公司電腦不允許 winget，可到 <https://ffmpeg.org/download.html> 下載 static build，解壓到例如 `C:\ffmpeg`，然後在程式的「設定 → 工具路徑」手動填 `C:\ffmpeg\bin\ffmpeg.exe` 與 `C:\ffmpeg\bin\ffprobe.exe`（見 §2.2）。

### 1.3 解壓交付包

把 `karaoke-vX.Y.Z.zip` 解壓到 `C:\Karaoke`（或其他簡短路徑），確認根目錄見到：

```
main.py    run_ui.bat    requirements.txt    requirements-ui.txt    ui\    assets\    docs\
```

### 1.4 雙擊 `run_ui.bat`

啟動器會依序做四件事，每一步都印 `[n/4]`，訊息是英文（`cmd.exe` 讀中文會變亂碼，所以刻意用英文）：

| 步驟 | 做什麼 | 失敗時 |
|---|---|---|
| `[1/4]` | 用 `py -3` 找 Python，並確認它有包 `tkinter` | 停住並印安裝指令（`tkinter` 是 UI 必需，Microsoft Store 版 Python 通常冇） |
| `[2/4]` | 在 PATH 找 `ffmpeg` / `ffprobe` | **只警告、不阻止**：首次執行會停住等你按鍵，之後每次只等 5 秒。可以照樣開 UI，之後在設定頁手動填路徑 |
| `[3/4]` | 建立 `.venv-ui\` | 停住（多數是磁碟空間或權限） |
| `[4/4]` | 依 `requirements.lock.txt` 安裝固定版本依賴；已裝過且 lock 未改就跳過 | 停住並提示多數是公司代理／SSL 攔截 |

第一次會停在黑色視窗 **1–3 分鐘**（下載約 200MB），屬正常。看到介面彈出來即代表成功。

日後再開：直接雙擊，幾秒內起 —— 因為 lock 檔沒變就完全跳過安裝。

> **失敗了怎麼辦**：黑色視窗會停住並顯示錯誤。把整段文字複製給交付方，不要自己亂刪資料夾。
> 最常見是公司網絡擋 pip（見 §5 的 `pip install` 條目）。

> 更新版本後（zip 覆蓋），啟動器會偵測到 `requirements.lock.txt` 變了，自動補裝依賴，唔需要你手動處理。

---

## 2 · 首次設定

雙擊 `run_ui.bat` 開窗後，左邊欄進入「設定」頁。所有改動離開本頁時自動寫入 `%USERPROFILE%\.karaoke\settings.json`。

### 2.1 API Key（必填）

1. 到阿里雲百煉控制台 → API-KEY 管理 → 建立新的 key（`sk-` 開頭）
2. 貼進「設定 → DashScope API Key」
3. 按「**測試連線**」，見到成功訊息才算通過

> Key 存在 `%USERPROFILE%\.karaoke\settings.json`（明文，僅本機使用者可讀）。
> 優先序：`--api-key` 參數 > 環境變數 `DASHSCOPE_API_KEY` > `.env` 檔 > 設定頁。
> 也就是說，**如果你自己在系統設了環境變數，設定頁填的 key 不會生效**，這是刻意設計。

### 2.2 工具路徑（通常自動填好）

「設定 → 工具路徑」應該已自動偵測到 FFmpeg / FFprobe。若兩格空白：

- 確認 §1.2 的 `ffmpeg -version` 通過
- 仍然空白就按「瀏覽…」手動指到 `...\ffmpeg\bin\ffmpeg.exe` 與 `ffprobe.exe`

### 2.3 預設資料夾（建議填）

「輸入資料夾」填你放 MV 的位置（例如 `D:\MV`），「輸出資料夾」填字幕與成品的去處。
填了之後每次開檔對話框會直接開在該處，省很多點擊。

### 2.4 外觀

三套主題可即時切換：`litera`（淺色，預設）/ `darkly`（深色）/ `superhero`（深藍）。

> 若交付方附了 `presets\home_style.json`，那是他調好的字幕樣式（字級、顏色、底部留白）。
> 兩個用法，任選其一：
>
> 1. **照填**（建議）：打開該檔，把數值逐項填到主頁「樣式」面板 —— 改完會自動記住。
> 2. **貼進設定**（給不怕改 JSON 的人）：關掉程式，用記事本打開
>    `%USERPROFILE%\.karaoke\settings.json`，把 `"home_style": { ... }` 的值換成該檔內容後儲存。
>
> 程式**不會**自動讀取 `presets\` 內的檔案 —— 這是刻意的，免得你日後改了樣式卻不知道為什麼又變回去。

---

## 3 · 冒煙測試（交付驗收，必做）

用一條 **30–60 秒**的短片測，不要拿整首 MV 試錯。

| # | 動作 | 通過標準 |
|---|---|---|
| 1 | 主頁按「選擇…」揀 MV | 畫面顯示解析度與時長（代表 ffprobe 正常） |
| 2 | 按「▶ 開始生成」 | 五階段（抽音 → 上傳 → 辨識 → 斷句 → 渲染）全部變綠 |
| 3 | 檢查輸出資料夾 | 出現 `<檔名>_timings.json` 與 `<檔名>_karaoke.ass` 兩個檔 |
| 4 | 按「立即開啟編輯器」，拖動一個字的時間，Ctrl+S | 有儲存提示，`.json` 有 `.bak` 備份 |
| 5 | 工具列按「🔥 燒錄」 | 產出 `<檔名>_karaoke.mp4`，用播放器確認字幕有出現、逐字變色正常 |

補充：

- 180 秒的 MV 實測約 **11 秒**完成辨識（上傳階段波動大，3 秒到 3 分鐘都屬正常）。
- 純音樂段落**本來就沒有字幕**，不是 bug。
- ASR 結果**每次跑都不同**（句數、標點位置會變），不是不穩定，是模型特性。

---

## 4 · 資料放在哪

| 內容 | 位置 | 備註 |
|---|---|---|
| 程式與 venv | `C:\Karaoke\` | 換新版時整個資料夾覆蓋 |
| 你的設定（含 API key） | `%USERPROFILE%\.karaoke\settings.json` | **不在程式資料夾內**，覆蓋程式不會洗掉 |
| 縮圖快取 | `%USERPROFILE%\.karaoke\thumbs\` | 可隨時刪，會自動重建 |
| 字幕專案（`*_timings.json` / `.ass` / `.mp4`） | 你揀的輸出資料夾 | 建議按歌名分資料夾 |

> `.karaoke` 是**跟 Windows 使用者帳號**綁的。同一台機換另一個 Windows 帳號登入，設定會是空的。

---

## 5 · 常見錯誤對照

| 訊息 / 現象 | 原因 | 處理 |
|---|---|---|
| `[error] 搵唔到 ffmpeg。` | 未裝，或裝了但 PATH 未生效 | 重開終端機；仍不行就在設定頁手動填路徑 |
| `[error] 搵唔到 DashScope API Key。` | 未貼 key | 見 §2.1 |
| `No module named 'tkinter'` | 用錯 venv（`.venv` 沒有 tkinter） | 一律用 `run_ui.bat` 或 `.venv-ui\Scripts\python.exe -m ui` |
| `pip install` 中途報 `SSLError` / `ProxyError` / `Read timed out` | 公司網絡或代理 | 開瀏覽器確認能上網、必要時設 `HTTPS_PROXY` 環境變數後重跑 `run_ui.bat` |
| `ASR 任務失敗：code=... message=...` | key 無效、欠費、或 model 名不對 | 設定頁按「測試連線」；到百煉控制台確認額度與 key 狀態 |
| `timings.json 的 style.primary_color 有問題` | json 被手改壞（顏色必須 6 位 hex） | 改回 `#RRGGBB` 格式，或刪掉該欄位讓它用預設值 |
| **燒出來完全沒有字幕，但 ffmpeg 沒報錯** | 手改過 `.ass`，或時間戳格式錯 | **不要改 `.ass`**：改 `*_timings.json` 後重新 `render`，再 `burn` |
| 字幕位置偏離、被 MV 原有字幕擋住 | 底部留白不足 | 主頁「樣式 → 底部留白」調大（例如 160）後重新燒錄 |
| 換了自備純音樂 MV 後字幕愈唱愈不對 | 兩個檔案不是同一剪輯版本 | 必須用**同一版本**的前奏尾奏長度；否則要回編輯器手動調時間軸 |

---

## 6 · 更新與回滾

### 更新

1. 拿到新 zip（例如 `karaoke-v1.1.0.zip`）
2. 先**關掉程式**
3. 解壓到**同一個資料夾**覆蓋（`C:\Karaoke`）
4. 雙擊 `run_ui.bat`

設定與 API key 不受影響（住在 `.karaoke`）。

若新版本改了依賴，`run_ui.bat` 會自己補裝。真的壞掉時，把 `.venv-ui` 資料夾改名成 `.venv-ui.bak`（或刪掉，它可完全重建），再跑一次 `run_ui.bat`。

### 回滾

保留上一個版本的 zip。出事就解壓回舊版覆蓋同一資料夾，設定同樣不受影響。

### 版本查詢

三個地方都會顯示同一個版本號（來源是根目錄的 `VERSION` 檔，唔會對唔上）：

| 位置 | 睇法 |
|---|---|
| 啟動視窗 | `run_ui.bat` 第一行：`Karaoke AI Subtitler   v1.0.4` |
| 介面 | 視窗標題列：`Karaoke AI Subtitler - Designed by Lamsir  (v1.0.4)` |
| 命令列 | `.venv-ui\Scripts\python.exe main.py --version` → `main.py 1.0.4` |

回報問題時請一併附上版本號，唔然冇辦法對照當時的 code。

---

## 7 · 紅線（唔好做）

| 不要 | 原因 |
|---|---|
| 把交付方的 `.venv` / `.venv-ui` 直接複製過去 | venv 內含絕對路徑，換機或換路徑就壞；一定要自己建 |
| 把程式放在 OneDrive / 桌面同步區 | 幾萬個小檔同步會拖死機，pip 可能鎖檔失敗 |
| 手改 `*_karaoke.ass` | 下次 `render` 會無條件覆寫；所有改動要落在 `*_timings.json` |
| 把 `.env` 或 `settings.json` 提交進 git / 貼到群組 | 內含 API key 明文 |
| 用別人的 API key | 額度與帳單算錯人，出事無法追查 |

---

## 8 · 給交付方（出包流程）

> 這一節給出包的人看，同事可以跳過。

### 8.1 已備好的元件

| 元件 | 檔案 | 作用 |
|---|---|---|
| 版本號 | `VERSION` | **唯一真源**：`main.py --version`、UI 標題、zip 檔名、啟動器 banner 全部讀它 |
| 版本讀取 | `ui/version.py` | `read_version()` / `window_title()`；讀唔到檔回 `dev`，唔會拋例外 |
| 鎖依賴 | `requirements.lock.txt` | 由 `.venv-ui` 的 `pip freeze` 產生（52 個套件，含傳遞依賴） |
| 啟動器 | `run_ui.bat` | 四段自檢：Python+tkinter → ffmpeg（只警告）→ venv → lock 安裝；用 `.venv-ui\.installed-lock` 比對，未變就完全跳過 |
| 可攜樣式 | `presets/home_style.json` | 你的字幕樣式數值，同事可照填或貼進 `settings.json` |
| 打包腳本 | `tools/build_release.py` | allowlist 打包 + 三項自檢（缺檔／禁區／金鑰） |
| 版本護欄 | `tools/version_guard.py`、`tools/hooks/pre-push` | 打包／推送前檢查 `VERSION` 有無跟住程式碼升 |
| 交付文件 | `DEPLOY.md` | 就是本文件 |

### 8.2 每次出包（三步）

**1. 升版號** —— 改 `VERSION` 一個檔（例如 `1.0.0` → `1.1.0`）。
唔好只改 zip 名：程式會自報舊版，同事回報問題時你就對唔上。

**2. 打包**

```bash
.venv-ui/Scripts/python.exe tools/build_release.py
```

生成 `dist/karaoke-v1.1.0.zip`，過程中自動檢查三件事（任何一項失敗即中止，唔會留低半成品）：

- 必要檔案齊全（`main.py` / `run_ui.bat` / `requirements.lock.txt` / `assets/app.ico` …）
- 冇夾帶禁區（`_probe/` / `_smoke/` / `.venv*` / `__pycache__` / 計劃書 / `.env`）
- 冇 API key 洩漏（掃 `sk-` 加 20 位以上字元，命中即中止）

想先睇清單唔寫檔：加 `--dry-run`。

**打包前會先跑版本護欄**：若 `VERSION` 之後仍有改過程式碼的 commit，會列出那幾個
commit 並提醒你先升版 —— 這正是「另一個對話改好功能但忘了升版」那個坑。
預設**只警告唔阻止**（文件類改動、試跑都不該被鎖死）；要硬性把關就加 `--strict`。

只改 `docs/`、任何 `*.md`、`VERSION`、`.gitignore`、`.gitattributes` 都**不算**需要升版。
相反，改 `requirements.lock.txt` **算** —— 因為它會改變同事那邊的安裝結果。

推送前也會檢查（前提是有 remote）：hook 已裝在 `tools/hooks/pre-push`，
repo 設定 `core.hooksPath=tools/hooks` 已生效。取消就 `git config --unset core.hooksPath`。

**3. 驗一驗**

```bash
.venv-ui/Scripts/python.exe tests/test_editor_core.py      # 應 ALL PASS (26 checks)
.venv-ui/Scripts/python.exe tests/test_version_guard.py    # 應 ALL PASS (8 checks)
```

### 8.3 依賴有變時

1. 在 `.venv-ui` 裝好新套件，確認程式跑得順
2. 重新產生 lock：

   ```bash
   .venv-ui/Scripts/python.exe -m pip freeze --exclude-editable | sort > requirements.lock.txt
   ```

   ⚠️ 這條命令會**覆蓋**檔案頂部那段說明註解，記得貼返（或先 `cp requirements.lock.txt` 留底再合併）。
3. 同事下次開程式會自動補裝 —— 啟動器見到 lock 內容變了就重裝。

### 8.4 打包前檢查清單

- `git status` 乾淨，冇未 commit 的 code
- 包內冇 `_probe/`、`_smoke/`、`.venv*`、`.workbuddy/`、`__pycache__`
- 包內冇 `.env`、`settings.json`
- `docs/` 只留 `使用說明.md`、`名詞表.md`、`架構決策紀錄.md`（計劃書與線框圖不外發）

### 8.5 交付包內容

```
karaoke-vX.Y.Z/
├── main.py  ui/  assets/  tests/  tools/
├── requirements.txt  requirements-ui.txt  requirements.lock.txt
├── run_ui.bat  VERSION
├── presets/home_style.json
├── README.md  DEPLOY.md
└── docs/使用說明.md  名詞表.md  架構決策紀錄.md
```

實測（v1.0.3）：47 個檔案、原始 0.60 MB、zip 0.26 MB。

### 8.6 三個交付時的提醒

- **實測時間**：全新 venv 裝 53 個套件約 1 分 45 秒（pip 有快取時；無快取約 3–5 分鐘），因為要下載約 200MB。第二次啟動約 10 秒。
- **測試會自動跳過部分檢查**：`tests/test_editor_core.py` 依賴 `_smoke/*_timings.json`（實測素材，`_smoke/` 不入版控），交付包內跑會 skip 需要素材的項目，這是預期行為。執行方式：`.venv-ui\Scripts\python.exe tests/test_editor_core.py`（不需要 pytest）。
- **口頭要講清楚三件事**：解壓到 `C:\Karaoke`、要自己申請 DashScope key、首次啟動要等幾分鐘裝依賴。

### 8.7 發佈到 GitHub Release

zip 出好之後，同事最方便是從 Release 下載，唔需要你傳檔。

1. 確認 §8.2 已完成（`VERSION` 已升、zip 已出）
2. 開 <https://github.com/hklamsir/karaoke/releases/new>
3. **Choose a tag** → 直接輸入 `v<VERSION>`（即 `v` + `VERSION` 檔內容）→ **Create new tag on publish**，target 選 `main`
4. Title：`v<VERSION> — 一句話講今次改動`
5. 描述貼變更摘要（可參考 v1.0.2 的寫法：功能分組 → 安裝四步 → 需求 → 已知限制）
6. 把 `dist\karaoke-v<VERSION>.zip` 拖到 **Attach binaries**
7. **Publish release**

驗證（`Content-Length` 要等於 zip 大小）：

```
curl -I -L https://github.com/hklamsir/karaoke/releases/download/v<VERSION>/karaoke-v<VERSION>.zip
```

> 下載連結格式固定為 `releases/download/v<VERSION>/karaoke-v<VERSION>.zip`。
> 只要 tag 名同 `VERSION` 一致，README 的 `releases/latest` 連結就永遠有效
> （GitHub 自動指向最新一個非 prerelease）。
>
> 本機未安裝 `gh` CLI，所以上面用網頁介面。若日後裝了 `gh`，等效命令：
> `gh release create v<VERSION> dist/karaoke-v<VERSION>.zip --title "..." --notes-file notes.md`
>
> 上面用 `<VERSION>` 而非寫死版本號：這一節每次出包都要照做一次，寫死的話
> 每次升版都要回頭改（v1.0.1 → v1.0.2 時就漏改過一次）。
