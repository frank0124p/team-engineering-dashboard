# 團隊工程產出儀表板（AI 時代）

每日監控團隊在 PR / commit / 部署上的產出，並把「AI 協助產出」當成第一級指標追蹤。
指標對齊 DORA、SPACE、DX Core 4 與 DX AI Measurement Framework。

目前跑在**示範資料**上（雛形），接上公司 Azure DevOps 只需改一個設定。

> **📐 每個指標的定義、計算公式與已知誤差，完整寫在 [METRICS.md](METRICS.md)。**
> 要解讀數字、或動任何指標之前，先讀那份。

```
team-dashboard/
├── config.json              ← 唯一要改的檔案
├── bin/
│   ├── dashboard.py         ← 進入點
│   ├── metrics.py           ← 指標計算（DORA / DX Core 4 / AI 框架）
│   ├── render.py            ← SVG 圖表元件
│   ├── page.py              ← 頁面組裝
│   └── sources/
│       ├── mock_source.py   ← 示範資料產生器
│       └── azdo_source.py   ← Azure DevOps REST adapter
└── out/
    ├── team-dashboard.html  ← 本機打開就能看
    ├── artifact-body.html   ← 發佈成線上 Artifact 用的片段
    └── metrics.json         ← 所有指標的原始數值
```

## 用法

```bash
cd ~/Claude/team-dashboard
/usr/bin/python3 bin/dashboard.py                 # 產生 HTML + JSON
/usr/bin/python3 bin/dashboard.py --summary       # 純文字摘要（丟 Discord 用）
/usr/bin/python3 bin/dashboard.py --window 14     # 改成看 14 天
/usr/bin/python3 bin/dashboard.py --source azdo   # 強制走 Azure DevOps
open out/team-dashboard.html
```

## 接上公司 Azure DevOps

### 1. 申請 PAT（唯讀即可）

Azure DevOps → 右上頭像 → **Personal access tokens** → New Token，勾這些 scope：

| Scope | 用途 |
|---|---|
| **Code (Read)** | repo、PR、commit、iteration |
| **Build (Read)** | 建置/部署紀錄 |
| **Release (Read)** | 走 Classic Release 的話需要 |
| **Work Items (Read)** | 工作類型配比（Impact 面向），可選 |

存進 Keychain（不要寫進檔案）：

```bash
security add-generic-password -s azdo-pat -a "$USER" -w '<貼上 PAT>'
```

### 2. 改 config.json

```json
{
  "source": "azdo",
  "azdo": {
    "organization": "你的組織名",
    "project": "你的專案名",
    "repos": [],                       // 空陣列 = 該專案下全部 repo
    "deployment_pipelines": ["xxx-release"],   // 空陣列 = 全部 pipeline
    "production_environments": ["Production"]
  }
}
```

### 3. 驗證

```bash
/usr/bin/python3 bin/dashboard.py --source azdo --summary
```

### 4.（可選）裝 Azure DevOps MCP，讓我能互動查詢

本機目前沒有這個 MCP。要加的話：

```bash
claude mcp add azure-devops -s user -- npx -y @azure-devops/mcp <你的組織名>
```

裝好後我可以直接問「這週誰的 PR 卡最久」而不用重跑整個管線。
儀表板本身不依賴 MCP（走 REST），所以排程照跑，兩條路互補。

## 排程（每日自動更新）

接上真實資料後，加到既有的 cron / launchd：

```bash
cd ~/Claude/team-dashboard && /usr/bin/python3 bin/dashboard.py --summary
```

把 stdout 丟進 Discord 就是每日工程早報；HTML 則留著給週會點開看細節。

## 指標清單（詳細定義見 [METRICS.md](METRICS.md)）

### DORA 四大指標（交付效能，團隊層級）
部署頻率、變更前置時間、變更失敗率、失敗復原時間。
分級門檻（Elite / High / Medium / Low）直接套 DORA State of DevOps 報告的定義。

### 流程指標（PR 健康度）
- **PR 週期時間**，拆成 撰寫 → 等待審查 → 審查中 → 待合併 四段。
  等待審查（pickup time）通常是最大的浪費來源。
- **PR 變更行數** 中位數與超標比例（業界健康上限 200–400 行）
- **審查覆蓋率**、**核准後回工率**、**審查集中度**（bus factor）、**積壓 PR**

### DX Core 4
Speed（吞吐與週期）、Quality（失敗率、回工率、PR 大小）、Impact（工作類型配比）。
Effectiveness 面向需要開發者體驗問卷（DXI），本工具不涵蓋。

### AI 指標（DX AI Measurement Framework）
- **採用率**：期間內至少交付一個 AI 協助 PR 的工程師佔比
- **AI 協助 PR 佔比**
- **AI vs 人工 對照**：週期時間、PR 大小、審查耗時、留言數、回工率、變更失敗率

AI 辨識方式：掃 commit message 的 `Co-Authored-By:` trailer
（Claude Code / Copilot / Cursor / Codex / Gemini），輔以 `ai-assisted` PR 標籤。
規則寫在 `config.json` 的 `ai_detection.trailers`，可自行增減。

> **已知限制**：工程師若手動刪掉 trailer，或直接在編輯器裡接受補全而不留痕跡，
> 這類 AI 使用抓不到，採用率會被低估。要拿精確數字得靠 IDE 遙測或問卷補。

## 使用守則（重要）

個人層級的數字**不可**當績效考核。DORA 與 SPACE 框架都指出，個人產出指標一旦變成 KPI，
就會被最佳化成「切小 PR、灌數量」而失去訊號（Goodhart 定律）。

成員視圖的設計用途是：
- 找出**週期時間特別長**的人 → 去問「你卡在哪」
- 找出**審查負載過度集中**的人 → 分擔出去
- 找出**完全沒參與審查**的人 → 把他拉進來

所以每一列都同時列出吞吐欄位與護欄欄位，不提供排序排名。


## Claude Code Skill

本機已安裝 skill，在 Claude Code 裡直接輸入 `/team-dashboard` 即可叫用。
Skill 定義在 `~/.claude/skills/team-dashboard/SKILL.md`（不在本 repo 內，因為它是本機工具設定）。

它知道怎麼跑這些指令、怎麼接 Azure DevOps、以及三條不可違反的報數字規則：
個人指標不做排名、AI 採用率必須標註低估、吞吐指標不可單獨報。
