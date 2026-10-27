#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""團隊工程產出儀表板 — 進入點。

用法：
  python3 bin/dashboard.py                      # 用 config.json 的來源，產 HTML + JSON
  python3 bin/dashboard.py --source azdo        # 強制用 Azure DevOps
  python3 bin/dashboard.py --artifact           # 另外輸出 Artifact 用的 body 片段
  python3 bin/dashboard.py --summary            # 印出純文字摘要（給 Discord / 早報用）
  python3 bin/dashboard.py --window 14          # 改觀測窗天數
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import metrics                      # noqa: E402
from page import build              # noqa: E402
from render import fmt_h, fmt_num   # noqa: E402


def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_data(cfg):
    if cfg["source"] == "azdo":
        from sources import azdo_source
        return azdo_source.fetch(cfg)
    from sources import mock_source
    return mock_source.fetch(cfg)


def text_summary(m):
    c, d = m["current"], m["deltas"]
    dora, flow, ai = c["dora"], c["flow"], c["ai"]

    def arrow(v, lower_better=False):
        if v is None or abs(v) < 1:
            return ""
        good = (v < 0) if lower_better else (v > 0)
        return f" {'↑' if v > 0 else '↓'}{abs(v):g}%{'' if good else ' ⚠'}"

    L = [f"📊 **{m['meta']['team']}** 工程產出 · 最近 {m['meta']['window_days']} 天",
         "",
         "**DORA**",
         f"· 部署頻率 {fmt_num(dora['deploy_freq_per_week'])} 次/週（{dora['ratings']['deploy_freq']}）"
         f"{arrow(d['dora.deploy_freq_per_week'])}",
         f"· 變更前置時間 {fmt_h(dora['lead_time_h'])}（{dora['ratings']['lead_time']}）"
         f"{arrow(d['dora.lead_time_h'], True)}",
         f"· 變更失敗率 {fmt_num(dora['change_failure_rate'], '%')}（{dora['ratings']['change_failure_rate']}）"
         f"{arrow(d['dora.change_failure_rate'], True)}",
         f"· 失敗復原 {fmt_h(dora['mttr_h'])}（{dora['ratings']['mttr']}）",
         "",
         "**流程**",
         f"· 合併 PR {flow['pr_merged']} 個 · 每人每週 {flow['pr_per_dev_week']}{arrow(d['flow.pr_per_dev_week'])}",
         f"· 週期時間中位 {fmt_h(flow['cycle_time_p50'])}{arrow(d['flow.cycle_time_p50'], True)}"
         f"（等待審查佔 {fmt_h(flow['breakdown']['pickup'])}）",
         f"· PR 行數中位 {fmt_num(flow['pr_size_p50'])}，超標者 {flow['pr_oversize_pct']}%",
         f"· 審查覆蓋 {flow['review_coverage_pct']}% · 回工率 {flow['rework_rate_pct']}% "
         f"· 積壓 PR {flow['stale_pr_count']} 個",
         "",
         "**AI**",
         f"· 採用率 {ai['adoption_pct']}%（{ai['adopters']}/{flow['active_devs']} 人）{arrow(d['ai.adoption_pct'])}",
         f"· AI 協助 PR 佔 {ai['ai_pr_pct']}%{arrow(d['ai.ai_pr_pct'])}"]
    if ai["ai"] and ai["human"]:
        a, h = ai["ai"], ai["human"]
        L.append(f"· AI vs 人工：週期 {fmt_h(a['cycle_p50'])} vs {fmt_h(h['cycle_p50'])}"
                 f"｜行數 {fmt_num(a['size_p50'])} vs {fmt_num(h['size_p50'])}"
                 f"｜失敗率 {fmt_num(a['cfr'], '%')} vs {fmt_num(h['cfr'], '%')}")

    alerts = []
    if flow["pr_oversize_pct"] > 30:
        alerts.append(f"PR 過大比例 {flow['pr_oversize_pct']}% 偏高")
    if flow["breakdown"]["pickup"] > m["meta"]["thresholds"]["pickup_time_target_hours"]:
        alerts.append(f"等待審查 {fmt_h(flow['breakdown']['pickup'])} 超過目標")
    if flow["reviewer_concentration_pct"] > 50:
        alerts.append(f"審查集中度 {flow['reviewer_concentration_pct']}%，負載過度集中")
    if flow["stale_pr_count"] > 5:
        alerts.append(f"{flow['stale_pr_count']} 個 PR 積壓超過 "
                      f"{m['meta']['thresholds']['stale_pr_days']} 天")
    if ai["ai"] and ai["human"] and ai["ai"]["cfr"] is not None and ai["human"]["cfr"] is not None \
            and ai["ai"]["cfr"] > ai["human"]["cfr"] + 2:
        alerts.append("AI PR 變更失敗率高於人工 PR，建議檢視 PR 大小與審查深度")
    if alerts:
        L += ["", "**⚠ 需要留意**"] + [f"· {a}" for a in alerts]
    if m["meta"]["source"] == "mock":
        L += ["", "_（目前為示範資料，尚未接上公司 Azure DevOps）_"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--source", choices=["mock", "azdo"])
    ap.add_argument("--window", type=int)
    ap.add_argument("--out")
    ap.add_argument("--artifact", action="store_true", help="另外輸出 Artifact 用的 body 片段")
    ap.add_argument("--summary", action="store_true", help="印出純文字摘要")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    if a.source:
        cfg["source"] = a.source
    if a.window:
        cfg["window_days"] = a.window

    data = get_data(cfg)
    m = metrics.compute(data, cfg)

    html_path = a.out or os.path.join(ROOT, cfg["output"]["html_path"])
    json_path = os.path.join(ROOT, cfg["output"]["json_path"])
    os.makedirs(os.path.dirname(html_path), exist_ok=True)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build(m, standalone=True))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    if a.artifact:
        frag = os.path.join(ROOT, "out", "artifact-body.html")
        with open(frag, "w", encoding="utf-8") as f:
            f.write(build(m, standalone=False))

    if a.summary:
        print(text_summary(m))
    elif not a.quiet:
        print(f"HTML: {html_path}")
        print(f"JSON: {json_path}")
        if a.artifact:
            print(f"ARTIFACT: {os.path.join(ROOT, 'out', 'artifact-body.html')}")


if __name__ == "__main__":
    main()
