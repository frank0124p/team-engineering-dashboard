# 團隊工程產出儀表板（AI 時代）

追蹤團隊在 PR／commit／部署上的產出，把「AI 協助產出」當成第一級指標。
指標對齊 DORA、SPACE、DX Core 4 與 DX AI Measurement Framework。

> **📐 每個指標的定義、公式與已知誤差寫在 [METRICS.md](METRICS.md)。**
> 要解讀數字、或動任何指標之前，先讀那一份。

**設計目標：搬到任何一台機器，只要兩件事就能跑 —— PAT 與要追蹤的成員名單。**
組織、專案、repo 清單都用 PAT 自動偵測；沒有 PAT 就跑示範資料，介面照樣完整。

---

## 三步上手

### 1. 申請 PAT（唯讀）

Azure DevOps → 右上頭像 → **Personal access tokens** → New Token：

| Scope | 用途 | 必要性 |
|---|---|---|
| **Code (Read)** | repo、PR、commit、iteration | 必要 |
| **Build (Read)** | 建置／部署紀錄 | 必要（DORA 要用） |
| **Release (Read)** | 走 Classic Release 的話 | 視環境 |
| **Work Items (Read)** | 工作類型配比 | 可選 |

### 2. 跑引導設定

```bash
/usr/bin/python3 bin/setup.py
```

它會：貼上 PAT → 驗證身分 → 自動列出你有權限的組織／專案／repo（只有一個就直接用）→
問要追蹤哪些成員 → 把設定寫進 `config.local.json`（已被 .gitignore 排除）。
PAT 存進 macOS Keychain；非 macOS 或存取失敗時寫入 `.env` 並 `chmod 600`。

不想互動也可以全部用環境變數（CI／容器適用）：

```bash
export AZDO_PAT='<PAT>'
export TD_MEMBERS='Amy Chen, ben.liu@corp.com, carol.wu'
export TD_ORG='my-org'          # 可省略，會自動偵測
export TD_PROJECT='my-project'  # 可省略
```

### 3. 跑

```bash
/usr/bin/python3 bin/dashboard.py --check      # 檢查設定與連線，列出缺什麼
/usr/bin/python3 bin/dashboard.py --summary    # 純文字摘要，可直接貼 Discord/Slack
/usr/bin/python3 bin/dashboard.py              # 產 HTML + JSON
open out/team-dashboard.html
```

---

## 設定從哪裡來

後面的覆蓋前面的，所以公司機器只要動第 2、3 層：

| 層 | 位置 | 放什麼 | 進 git？ |
|---|---|---|---|
| 1 | `config.json` | 預設值、門檻、AI 偵測規則 | ✅ 會 |
| 2 | `config.local.json` | 你的組織／專案／成員名單 | ❌ 已排除 |
| 3 | 環境變數 `TD_*`、`AZDO_PAT` | CI／容器 | ❌ |
| 4 | 命令列 `--members/--org/--project/--window` | 臨時試一下 | ❌ |

PAT 的來源依序是：環境變數 → `.env` → macOS Keychain。
**任何一層都不會把 PAT 寫進會進 git 的檔案。**

---

## 成員名單怎麼寫

email、帳號、顯示名混著寫都認得（正規化後比對，大小寫、空白、點、底線都不影響）：

```json
"team": {
  "name": "Platform Team",
  "members": ["Amy Chen", "ben.liu@corp.com", "carol.wu"],
  "members_mode": "include"
}
```

- **名單有填** → 所有指標、個人區塊、積壓 PR 清單都只算名單內的人
- **名單留空** → 計入專案內所有人（機器人仍會排除，清單在 `config.json` 頂層 `bot_patterns`）
- **名字打錯會講出來**：名單裡有人整段期間零 PR，摘要與 HTML 都會點名提醒 ——
  這是實務上最常見的坑（換帳號、改英文名、拼錯）
- **名單外出現的人也會列出**，方便你決定要不要納入

臨時只看某幾個人不必改檔：

```bash
/usr/bin/python3 bin/dashboard.py --summary --members "Amy Chen, ben.liu"
```

---

## 指令

```bash
bin/dashboard.py --check             # 設定與連線體檢（搬機器後第一件事）
bin/dashboard.py --summary           # 純文字摘要
bin/dashboard.py --window 7          # 改觀測窗天數
bin/dashboard.py --source demo       # 強制跑示範資料（給簡報／試版面用）
bin/dashboard.py --source azdo       # 強制走 Azure DevOps
bin/dashboard.py --artifact          # 另產 out/artifact-body.html（發佈線上版用）
bin/setup.py                         # 互動設定
bash bin/daily.sh                    # 重建 + 印摘要（排程用）
```

`--source` 預設是 `auto`：拿得到 PAT 就連 Azure DevOps，拿不到就跑示範資料並在輸出標明。

---

## 產物

| 檔案 | 用途 |
|---|---|
| `out/team-dashboard.html` | 本機打開就能看，單一檔案、無外部依賴 |
| `out/metrics.json` | 所有指標的原始數值，要自己加工就讀這支 |
| `out/artifact-body.html` | 發佈成線上版用的 body 片段 |

---

## 搬到公司環境的注意事項

- **只依賴 Python 標準庫**，不需要 pip install。`/usr/bin/python3`（3.9+）就能跑。
- **不綁 macOS**：Keychain 只是 PAT 的其中一個選項，Windows／Linux 走環境變數或 `.env`。
- **全部唯讀**：只呼叫 GET，不會對公司 repo 或 pipeline 做任何寫入。
- **PR 大小的單位是「變更檔案數」**，不是行數 —— Azure DevOps 的 PR API 不給行數。
  詳情與取捨見 METRICS.md 的 [2.4](METRICS.md#24-pr-大小)。
- **指標不拆到個人績效**：個人區塊存在的目的是找「誰被卡住、誰的審查負載過重」，
  METRICS.md 第 6 節寫明了哪些用法會把這份儀表板變成反效果，上線前請團隊一起讀那一節。

---

## 檔案結構

```
team-dashboard/
├── config.json              預設值（進 git，不含機密與公司資訊）
├── config.local.json        你的設定（setup.py 產生，已 gitignore）
├── .env                     PAT（可選，已 gitignore）
├── bin/
│   ├── setup.py             互動設定精靈
│   ├── dashboard.py         進入點
│   ├── config_loader.py     設定分層載入 + 成員名單比對
│   ├── azdo_client.py       Azure DevOps HTTP 層 + 組織/專案自動偵測
│   ├── metrics.py           指標計算（DORA / DX Core 4 / AI 框架）
│   ├── page.py              HTML 組裝
│   ├── render.py            SVG 圖表元件
│   └── sources/
│       ├── azdo_source.py   Azure DevOps → 正規化資料
│       └── mock_source.py   示範資料產生器
├── METRICS.md               指標定義與已知誤差
└── out/                     產物（gitignore）
```
