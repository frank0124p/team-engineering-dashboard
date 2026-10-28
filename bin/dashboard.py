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

import config_loader                # noqa: E402
import metrics                      # noqa: E402
from page import build              # noqa: E402
from render import fmt_h, fmt_num   # noqa: E402


def get_data(cfg, force=None):
    """預設自動判斷：拿得到 PAT 就走 Azure DevOps，拿不到就跑示範資料。"""
    from azdo_client import AzdoError
    from config_loader import resolve_pat
    mode = force or cfg.get("source") or "auto"
    if mode == "demo":
        from sources import mock_source
        return mock_source.fetch(cfg)
    if mode is None or mode == "auto":
        tok, _ = resolve_pat(cfg)
        if tok:
            mode = "azdo"
        else:
            print("⚠️  找不到 PAT，先用示範資料。設定方式：/usr/bin/python3 bin/setup.py",
                  file=sys.stderr)
            mode = "demo"
    if mode == "azdo":
        from sources import azdo_source
        try:
            return azdo_source.fetch(cfg)
        except AzdoError as e:
            print(f"❌ Azure DevOps 讀取失敗：{e}", file=sys.stderr)
            raise SystemExit(1)
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
         f"· PR 大小中位 {fmt_num(flow['pr_size_p50'])}{flow['size_unit']}，"
         f"超標者 {flow['pr_oversize_pct']}%",
         f"· 審查覆蓋 {flow['review_coverage_pct']}% · 回工率 {flow['rework_rate_pct']}% "
         f"· 積壓 PR {flow['stale_pr_count']} 個",
         "",
         "**AI**",
         f"· 採用率 {ai['adoption_pct']}%（{ai['adopters']}/{flow['active_devs']} 人）{arrow(d['ai.adoption_pct'])}",
         f"· AI 協助 PR 佔 {ai['ai_pr_pct']}%{arrow(d['ai.ai_pr_pct'])}"]
    if ai["ai"] and ai["human"]:
        a, h = ai["ai"], ai["human"]
        L.append(f"· AI vs 人工：週期 {fmt_h(a['cycle_p50'])} vs {fmt_h(h['cycle_p50'])}"
                 f"｜大小 {fmt_num(a['size_p50'])} vs {fmt_num(h['size_p50'])}{flow['size_unit']}"
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
    sc = m["meta"].get("scope") or {}
    if m["meta"]["source"] == "mock":
        L += ["", "_（示範資料。執行 bin/setup.py 接上 Azure DevOps）_"]
    elif sc.get("organization"):
        L += ["", f"_{sc['organization']} / {sc['project']}・{len(sc.get('repos') or [])} 個 repo_"]
    rr = m["meta"].get("roster_report") or {}
    if rr.get("missing"):
        L += [f"_⚠️ 名單裡這幾位沒對到任何 PR：{'、'.join(rr['missing'][:5])}_"]
    return "\n".join(L)


def doctor(cfg):
    """搬到新機器第一件事：看現在能不能跑、缺什麼。"""
    ok, blockers, warnings = config_loader.ready(cfg)
    az = cfg.get("azdo", {})
    tok, tok_src = config_loader.resolve_pat(cfg)
    members = cfg["team"].get("members") or []
    print("📋 設定檢查")
    print(f"  資料來源      {cfg.get('source') or 'auto'}")
    print(f"  PAT           {'✅ ' + tok_src if tok else '❌ 沒找到'}")
    print(f"  組織／專案    {az.get('organization') or '（留空，連線時自動偵測）'}"
          f" / {az.get('project') or '（自動）'}")
    print(f"  Repo 範圍     {', '.join(az.get('repos') or []) or '全部'}")
    print(f"  追蹤成員      {len(members)} 人" + (f"：{', '.join(members[:8])}" if members else "（空＝全專案）"))
    print(f"  PR 大小單位   {cfg.get('pr_size_metric', 'files')}")
    print(f"  觀測窗        {cfg.get('window_days')} 天（對照前 {cfg.get('baseline_days')} 天）")

    if tok:
        try:
            from azdo_client import Client, discover
            scope, me = discover(Client(tok), cfg)
            print(f"\n🔌 連線正常：{me} → {scope['organization']} / {scope['project']}"
                  f"・{len(scope.get('repos') or [])} 個 repo")
        except Exception as e:
            print(f"\n⚠️ PAT 找到了，但連線失敗：{e}")
            ok = False
    for w in warnings:
        print(f"\nℹ️ {w}")
    if ok:
        print("\n✅ 可以跑了：/usr/bin/python3 bin/dashboard.py --summary")
    else:
        print("\n⚠️ 還缺：")
        for b in blockers:
            print(f"  ・{b}")
        print("\n跑一次引導設定就好：/usr/bin/python3 bin/setup.py")
    return 0 if ok else 1


def roster_note(m):
    """名單比對結果 —— 名字拼錯是最常見的災難，要講出來。"""
    r = (m.get("meta") or {}).get("roster_report") or {}
    if not r.get("members"):
        return []
    out = [f"👥 追蹤名單：{len(r['members'])} 人（{r.get('mode', '')}）"]
    if r.get("missing"):
        out.append(f"  ⚠️ 這段期間沒有任何合併 PR：{'、'.join(r['missing'])}"
                   f"（確認名字拼法或帳號有沒有換）")
    if r.get("not_in_roster"):
        top = "、".join(f"{n}（{c}）" for n, c in r["not_in_roster"][:5])
        out.append(f"  ℹ️ 名單外還看到：{top} —— 要納入就加進 team.members")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--source", choices=["azdo", "demo", "auto"],
                    help="預設 auto：有 PAT 走 azdo，沒有就示範資料")
    ap.add_argument("--window", type=int)
    ap.add_argument("--out")
    ap.add_argument("--ai", action="store_true",
                    help="跑 AI 品質評估並併進儀表板（會呼叫 Claude CLI，需要時間與費用）")
    ap.add_argument("--artifact", action="store_true", help="另外輸出 Artifact 用的 body 片段")
    ap.add_argument("--summary", action="store_true", help="印出純文字摘要")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--check", action="store_true", help="只檢查設定與連線，不產報表")
    ap.add_argument("--members", help="臨時覆蓋追蹤名單（逗號分隔）")
    ap.add_argument("--org", help="Azure DevOps 組織（平常不用填，會自動偵測）")
    ap.add_argument("--project", help="Azure DevOps 專案（同上）")
    a = ap.parse_args()

    cfg = config_loader.load(a.config, {"window_days": a.window})
    if a.org:
        cfg.setdefault("azdo", {})["organization"] = a.org
    if a.project:
        cfg.setdefault("azdo", {})["project"] = a.project
    if a.members:
        cfg.setdefault("team", {})["members"] = config_loader.split_list(a.members)
    if a.check:
        return doctor(cfg)

    data = get_data(cfg, a.source)
    m = metrics.compute(data, cfg)

    html_path = a.out or os.path.join(ROOT, cfg["output"]["html_path"])
    json_path = os.path.join(ROOT, cfg["output"]["json_path"])
    os.makedirs(os.path.dirname(html_path), exist_ok=True)

    ai = None
    ai_path = os.path.join(ROOT, "out", "ai_insight.json")
    if a.ai:
        import ai_insight
        print("🤖 執行 AI 品質評估…", file=sys.stderr)
        ai = ai_insight.run(cfg, m, data)
        json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    elif os.path.exists(ai_path):
        # 沒帶 --ai 時沿用上次的結果，並在頁面標明產生時間
        try:
            ai = json.load(open(ai_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ai = None

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build(m, standalone=True, insight=ai))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    if a.artifact:
        frag = os.path.join(ROOT, "out", "artifact-body.html")
        with open(frag, "w", encoding="utf-8") as f:
            f.write(build(m, standalone=False, insight=ai))

    if a.summary:
        print(text_summary(m))
        note = roster_note(m)
        if note:
            print("\n" + "\n".join(note))
    elif not a.quiet:
        print(f"HTML: {html_path}")
        print(f"JSON: {json_path}")
        if a.artifact:
            print(f"ARTIFACT: {os.path.join(ROOT, 'out', 'artifact-body.html')}")


if __name__ == "__main__":
    sys.exit(main() or 0)
