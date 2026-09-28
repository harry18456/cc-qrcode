[English](README.md) | 繁體中文

# qrcode

讓 Claude Code 設計**保證掃得到**的 QR code:中間放 logo、品牌色或漸層、圓點 / 圓角 / 葉片碼點、自訂定位框造形、小裝飾,以及含文字說明的桌卡版面。能客觀檢查的決定全交給**確定性引擎**;Claude 只負責寫設定檔(spec)、準備圖片、判斷美感。

裝好後用自然語言提出需求就會觸發,不必記指令:

- 「幫活動頁做一個 QR code,中間放 logo,印 3 公分寬」
- 「做 Wi-Fi 的 QR,圓點、咖啡色,放在櫃台平板上」
- 「這個 QR 還掃得到嗎?」(別處做好的圖)
- 「印在名片上的話,logo 最大可以多大?」

特色:

- **容錯照解碼器的方式計算**:以 Reed-Solomon block 為單位、每 8 個模組一個 codeword,不看面積 —— 只佔 9% 面積的 logo 就可能吃掉某個 block 一半的容錯。渲染前就算出最大安全 logo。
- **由引擎決定**:容錯等級、版本、遮罩、logo 尺寸、底板形狀、裝飾位置、配色修正 —— 同一份 spec 每次產出相同結果。
- **每次建置都過關卡**:每個 block 容錯用量 ≤ 50%、結構完整、定位框維持 1:1:3:1:1、對比 ≥ 0.60、zxing 與 zbar 都能解碼、在模糊 / 縮小 / 傾斜 / 曲面 / JPEG / 褪色下的耐受度 ≥ 一般 QR 的 70%、每模組 4–48 px 都能解碼、印刷模組 ≥ 0.4 mm。
- **失敗會附數字**:報告算出最小的讓步(最大可放的 logo、需要的印刷寬度、內容要刪幾個 byte),並指出是哪個樣式元素造成失敗。
- **`explore`**:幾秒內出取捨表,不必渲染。**`card`**:桌卡、平板畫面,含標題、說明文字與 Wi-Fi 資訊 —— 完成的卡片會再解碼一次。
- **圖片**:用你提供的檔案;沒有的話,Claude 會先問你要不要提供,再自己畫簡單的 SVG 圖示。所有圖片都過同一套關卡。

跨平台(Windows / macOS / Linux):所有渲染都走 wheel 安裝的 `resvg`,不需要 cairo 等系統繪圖函式庫。有 uv 或只有一般 Python 都能執行;兩者裝到相同的鎖定版本,產出的檔案逐位元相同。

## 需求

- 以下任一:
  - [uv](https://docs.astral.sh/uv/getting-started/installation/)(建議;Python 與引擎的套件都由它自動取得)
  - Python 3.12–3.14,需含 `venv`(Debian/Ubuntu 請裝 `python3-venv`)
- Claude Code

## 安裝

```
/plugin marketplace add harry18456/cc
/plugin install qrcode@harry18456
```

重開 Claude Code 生效。

### 本機立即使用(免 push、免 marketplace)

把 skill 目錄連進 skills 目錄,下個 session 就會載入:

```powershell
# Windows
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\qrcode" -Target "<這個 repo 的路徑>\skills\qrcode"
```

```bash
# macOS / Linux
ln -s "<這個 repo 的路徑>/skills/qrcode" ~/.claude/skills/qrcode
```

## 第一次使用注意

- 第一次執行會安裝引擎的套件(segno、numpy、opencv、zxing-cpp、pyzbar、resvg、PyMuPDF,解壓後約 230 MB,需 1–3 分鐘)。系統 Python 完全不受影響:
  - 有 uv:套件放在 **uv 自己的快取**;
  - 沒有 uv:引擎用 pip 從附雜湊的 wheel 建一個**私用環境**,位置在 `%USERPROFILE%\.cache\qrcode-skill\`(Windows)、`~/Library/Caches/qrcode-skill/`(macOS)或 `~/.cache/qrcode-skill/`(Linux)。刪掉這個資料夾就能釋放空間,下次執行會自動重建。
- 所有版本都鎖在 `qrart.py.lock`(沒有 uv 時改用由它匯出的 `requirements.lock.txt`),套件出新版也不會改變同一份 spec 的渲染結果。
- 一次建置約 10–60 秒;自我測試約 2 分鐘。
- pyzbar 的 Windows wheel 已內含 zbar。若仍載入失敗,請安裝 Microsoft Visual C++ 2013 Redistributable (x64);macOS:`brew install zbar`(Apple Silicon 另需 `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`);Linux:`apt-get install libzbar0`。沒有 zbar 時只用 zxing 建置,報告會註明。
- 卡片上的中文 / 日文字需要 CJK 字型(Windows 與 macOS 內建;Linux 請裝 Noto Sans CJK)。

## 運作方式

`skills/qrcode/scripts/qrart.py` 是引擎;Claude 透過 YAML spec 驅動它:

1. Claude 把需求寫成 spec —— 內容、顏色、主題、圖片、印刷寬度 —— 引擎能決定的都留 `auto`。
2. 引擎編碼內容,把每個模組對應到它的 codeword 與 Reed-Solomon block,再搜尋符號大小、容錯等級、遮罩、logo 尺寸與裝飾位置,找出餘裕最大的設計。
3. 渲染成一份 SVG —— SVG、PNG、PDF 三種檔案與所有驗證影像都出自它 —— 接著跑上面的關卡,寫出 `report.md` / `report.json`。
4. 失敗時報告列出最小的讓步;Claude 拿這些數字跟你討論,而不是放寬門檻。所有門檻都在 `assets/thresholds.yaml`。

PDF 內嵌印刷尺寸的點陣圖(1200 dpi),和 PNG 一樣從同一份 SVG 產生,寫出前會再解碼一次。向量檔請用 SVG,交給印刷廠或設計師。

## 限制

- 手機掃描器(iOS 相機、Google Lens、通訊軟體)是封閉的:最後一步 —— 印出來、在實際距離掃描 —— 要由你來做。報告附有檢查清單。
- 掃描距離在你把手機實測結果寫進校正檔(`--calibration`)之前,都只是經驗公式。
- 50% / 70% / 0.4 mm 這些門檻是引擎依解碼器測試訂的保守起始值,不是外部標準。
- 不做 AI 生成的「藝術 QR」(ControlNet 類):它無法保證結構正確。

## 本地開發 / 測試

不必先推上 GitHub,直接載入這個資料夾:

```
claude --plugin-dir <這個 repo 的路徑>
```

引擎回歸測試(改了程式或門檻之後執行):

```
uv run skills/qrcode/scripts/qrart.py selftest      # 或:python skills/qrcode/scripts/qrart.py selftest
```

改了相依套件之後,重新鎖定,再匯出給「沒有 uv」路徑用的版本清單(自我測試會檢查兩者一致):

```
cd skills/qrcode/scripts
uv lock --script qrart.py
uv export --script qrart.py --format requirements-txt --no-header --quiet -o requirements.lock.txt
```

## License

MIT
