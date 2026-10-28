"""指標計算 — 對齊業界標準框架。

採用的框架與出處：
  • DORA（Accelerate / State of DevOps）四大交付指標 + 官方績效分級門檻
  • DX Core 4（Speed / Effectiveness / Quality / Impact）— 2024 年由 DX 提出，
    把 DORA、SPACE、DevEx 收斂成四個面向
  • SPACE 框架 — 強調「不要只看 Activity」，所以每個吞吐指標都配一個品質/體驗護欄
  • DX AI Measurement Framework（2025）— AI 採用率、AI 輔助產出佔比、
    以及「AI PR vs 人工 PR」的品質對照

設計原則：所有個人層級數字只做「對話素材」，不做排名績效，
         因此人員表一律附上護欄欄位（PR 大小、回工率），避免只看數量。
"""
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


# ---------- 小工具 ----------

def dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def hours(a, b):
    if not a or not b:
        return None
    return (dt(b) - dt(a)).total_seconds() / 3600.0


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def p(values, q):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 2)
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 2)


# ---------- DORA 分級 ----------
# 門檻取自 DORA State of DevOps 報告（Elite / High / Medium / Low）

def rate_deploy_freq(per_day):
    if per_day is None:
        return "—"
    if per_day >= 1:          return "Elite"      # 一天多次～每天
    if per_day >= 1 / 7:      return "High"       # 每週一次～每天
    if per_day >= 1 / 30:     return "Medium"     # 每月一次～每週
    return "Low"


def rate_lead_time(h):
    if h is None:             return "—"
    if h < 24:                return "Elite"      # < 1 天
    if h < 24 * 7:            return "High"       # < 1 週
    if h < 24 * 30:           return "Medium"     # < 1 個月
    return "Low"


def rate_cfr(x):
    if x is None:             return "—"
    if x <= 5:                return "Elite"
    if x <= 10:               return "High"
    if x <= 15:               return "Medium"
    return "Low"


def rate_mttr(h):
    if h is None:             return "—"
    if h < 1:                 return "Elite"      # < 1 小時
    if h < 24:                return "High"       # < 1 天
    if h < 24 * 7:            return "Medium"     # < 1 週
    return "Low"


RATING_STATUS = {"Elite": "good", "High": "good", "Medium": "warning", "Low": "critical", "—": "muted"}


# ---------- AI 偵測 ----------

def detect_ai(pr, cfg):
    """回傳 (是否 AI 協助, 工具名稱)。以 commit trailer 為主，PR 標籤為輔。"""
    rules = cfg["ai_detection"]["trailers"]
    blob = "\n".join(c.get("message", "") or "" for c in pr.get("commits", []))
    for r in rules:
        if re.search(r["pattern"], blob):
            return True, r["tool"]
    labels = {str(l).lower() for l in (pr.get("labels") or [])}
    if labels & {str(x).lower() for x in cfg["ai_detection"].get("pr_labels", [])}:
        return True, "Labelled"
    return False, None


def roster_of(cfg):
    """取得（必要時建立）這次計算共用的成員名單物件。"""
    r = cfg.get("_roster")
    if r is None:
        import config_loader
        r = config_loader.Roster(cfg)
        cfg["_roster"] = r
    return r


def is_tracked(x, cfg):
    """這筆 PR 的作者在追蹤名單內嗎（名單空白時全部計入）。"""
    return roster_of(cfg).includes(x.get("author"), x.get("author_name"))


def is_bot(name, cfg):
    """azdo 來源已經在抓取時用團隊名單濾過了；這裡是 mock 與舊資料的保險。"""
    n = (name or "").lower()
    return any(b in n for b in (cfg.get("bot_patterns") or []))


# ---------- 單一期間的指標 ----------

def size_of(pr, metric):
    """PR 大小。Azure DevOps 不提供增刪行數，所以預設用變更檔案數 —— 
    準確的小單位勝過估出來的大單位。"""
    if metric == "lines":
        return (pr.get("additions") or 0) + (pr.get("deletions") or 0)
    return pr.get("files_changed") or 0


def size_threshold(cfg, metric):
    th = cfg["thresholds"]
    return (th.get("pr_size_healthy_lines", 400) if metric == "lines"
            else th.get("pr_size_healthy_files", 10))


SIZE_UNIT = {"files": "個檔", "lines": "行"}


def _window_prs(prs, start, end):
    """以 merge 時間落在視窗內為準（未合併的 PR 另外用 created_at 看積壓）。"""
    return [x for x in prs if x.get("merged_at") and start <= dt(x["merged_at"]) < end]


def period(data, cfg, start, end, label):
    metric = data.get("size_metric", cfg.get("pr_size_metric", "files"))
    size_limit = size_threshold(cfg, metric)
    prs_all = data["pull_requests"]
    prs = [x for x in _window_prs(prs_all, start, end)
           if not is_bot(x.get("author_name"), cfg) and is_tracked(x, cfg)]
    deps = [d for d in data["deployments"] if start <= dt(d["finished_at"]) < end]
    incs = [i for i in data["incidents"] if start <= dt(i["started_at"]) < end]
    days = max(1, (end - start).days)
    th = cfg["thresholds"]

    for x in prs:
        x["_ai"], x["_tool"] = detect_ai(x, cfg)
        x["_coding"] = hours(x.get("first_commit_at"), x.get("created_at"))
        x["_pickup"] = hours(x.get("created_at"), x.get("first_review_at"))
        x["_review"] = hours(x.get("first_review_at"), x.get("first_approval_at"))
        x["_idle"] = hours(x.get("first_approval_at"), x.get("merged_at"))
        x["_cycle"] = hours(x.get("created_at"), x.get("merged_at"))
        x["_lead"] = hours(x.get("first_commit_at"), x.get("merged_at"))
        x["_size"] = size_of(x, metric)

    authors = {x["author"] for x in prs}
    devs = max(1, len(authors))
    weeks = days / 7.0

    # ---- DORA ----
    succeeded = [d for d in deps if d["result"] == "succeeded"]
    freq = len(succeeded) / days if deps else None
    lead = p([x["_lead"] for x in prs], 0.5)
    cfr = pct(len([d for d in deps if d["result"] == "failed"]), len(deps)) if deps else None
    mttr = p([hours(i["started_at"], i["resolved_at"]) for i in incs], 0.5)

    dora = {
        "deploy_freq_per_day": round(freq, 2) if freq is not None else None,
        "deploy_freq_per_week": round(freq * 7, 1) if freq is not None else None,
        "deploy_count": len(succeeded),
        "lead_time_h": lead,
        "change_failure_rate": cfr,
        "mttr_h": mttr,
        "ratings": {
            "deploy_freq": rate_deploy_freq(freq),
            "lead_time": rate_lead_time(lead),
            "change_failure_rate": rate_cfr(cfr),
            "mttr": rate_mttr(mttr),
        },
    }

    # ---- 流程 / Speed ----
    reviewed = [x for x in prs if x.get("first_review_at")]
    def _is_stale(x):
        if x.get("status") != "active" or not x.get("created_at"):
            return False
        if dt(x["created_at"]) >= end:
            return False
        if hours(x["created_at"], end.isoformat()) <= th["stale_pr_days"] * 24:
            return False
        if is_bot(x.get("author_name"), cfg):
            return False
        # 積壓清單也要只看追蹤名單內的人，不然會看到別組的 PR
        r = roster_of(cfg)
        return (not r.enabled) or r.match(x.get("author"), x.get("author_name")) is not None

    stale = [x for x in prs_all if _is_stale(x)]
    sizes = [x["_size"] for x in prs]
    reviewer_load = Counter()
    for x in prs:
        for r in x.get("reviewers", []):
            reviewer_load[r] += 1
    top2 = sum(n for _, n in reviewer_load.most_common(2))
    flow = {
        "pr_merged": len(prs),
        "pr_per_dev_week": round(len(prs) / devs / weeks, 2),
        "active_devs": devs,
        "cycle_time_p50": p([x["_cycle"] for x in prs], 0.5),
        "cycle_time_p75": p([x["_cycle"] for x in prs], 0.75),
        "lead_time_p50": lead,
        "breakdown": {
            "coding": p([x["_coding"] for x in prs], 0.5) or 0,
            "pickup": p([x["_pickup"] for x in prs], 0.5) or 0,
            "review": p([x["_review"] for x in prs], 0.5) or 0,
            "merge": p([x["_idle"] for x in prs], 0.5) or 0,
        },
        "pr_size_p50": p(sizes, 0.5),
        "pr_size_p75": p(sizes, 0.75),
        "pr_oversize_pct": pct(len([s for s in sizes if s > size_limit]), len(sizes)),
        "review_coverage_pct": pct(len(reviewed), len(prs)),
        "rework_rate_pct": pct(len([x for x in prs if x.get("pushes_after_approval", 0) > 0]), len(prs)),
        "stale_pr_count": len(stale),
        "stale_prs": sorted(
            [{"id": x["id"], "repo": x["repo"], "title": x["title"], "author": x.get("author_name"),
              "age_days": round(hours(x["created_at"], end.isoformat()) / 24, 1)} for x in stale],
            key=lambda z: -z["age_days"])[:10],
        "reviewer_concentration_pct": pct(top2, sum(reviewer_load.values())),
        "size_histogram": _histogram(sizes, metric),
        "size_metric": metric,
        "size_unit": SIZE_UNIT.get(metric, ""),
        "size_limit": size_limit,
    }

    # ---- Impact（工作類型配比）----
    wt = Counter(x.get("work_item_type") or "Unclassified" for x in prs)
    flow["work_mix"] = [{"type": k, "count": v, "pct": pct(v, len(prs))} for k, v in wt.most_common()]

    # ---- AI 指標 ----
    ai_prs = [x for x in prs if x["_ai"]]
    hu_prs = [x for x in prs if not x["_ai"]]
    adopters = {x["author"] for x in ai_prs}
    tools = Counter(x["_tool"] for x in ai_prs if x["_tool"])

    def _cmp(group):
        if not group:
            return None
        g_deps = [d for d in deps if d.get("pr_id") in {x["id"] for x in group}]
        return {
            "count": len(group),
            "cycle_p50": p([x["_cycle"] for x in group], 0.5),
            "size_p50": p([x["_size"] for x in group], 0.5),
            "review_h_p50": p([x["_review"] for x in group], 0.5),
            "comments_p50": p([x.get("comment_count", 0) for x in group], 0.5),
            "rework_pct": pct(len([x for x in group if x.get("pushes_after_approval", 0) > 0]), len(group)),
            "cfr": pct(len([d for d in g_deps if d["result"] == "failed"]), len(g_deps)) if g_deps else None,
        }

    ai = {
        "adoption_pct": pct(len(adopters), devs),
        "adopters": len(adopters),
        "ai_pr_pct": pct(len(ai_prs), len(prs)),
        "ai_pr_count": len(ai_prs),
        "by_tool": [{"tool": k, "count": v, "pct": pct(v, len(ai_prs))} for k, v in tools.most_common()],
        "ai": _cmp(ai_prs),
        "human": _cmp(hu_prs),
    }

    # ---- 人員層級（對話素材，非績效排名）----
    people_name = {pp["id"]: pp.get("name", pp["id"]) for pp in data.get("people", [])}
    by_person = defaultdict(list)
    for x in prs:
        by_person[x["author"]].append(x)
    people = []
    for uid, items in by_person.items():
        a = [x for x in items if x["_ai"]]
        people.append({
            "id": uid,
            "name": items[0].get("author_name") or people_name.get(uid, uid),
            "team": items[0].get("team", ""),
            "pr_merged": len(items),
            "pr_per_week": round(len(items) / weeks, 2),
            "cycle_p50": p([x["_cycle"] for x in items], 0.5),
            "size_p50": p([x["_size"] for x in items], 0.5),
            "ai_pct": pct(len(a), len(items)),
            "reviews_given": reviewer_load.get(uid, 0),
            "rework_pct": pct(len([x for x in items if x.get("pushes_after_approval", 0) > 0]), len(items)),
            "oversize_pct": pct(len([x for x in items if x["_size"] > size_limit]), len(items)),
        })
    people.sort(key=lambda z: -z["pr_merged"])

    # ---- Repo 層級 ----
    by_repo = defaultdict(list)
    for x in prs:
        by_repo[x["repo"]].append(x)
    repos = sorted([{
        "repo": r,
        "pr_merged": len(items),
        "cycle_p50": p([x["_cycle"] for x in items], 0.5),
        "size_p50": p([x["_size"] for x in items], 0.5),
        "ai_pct": pct(len([x for x in items if x["_ai"]]), len(items)),
    } for r, items in by_repo.items()], key=lambda z: -z["pr_merged"])

    return {"label": label, "start": start.isoformat(), "end": end.isoformat(), "days": days,
            "dora": dora, "flow": flow, "ai": ai, "people": people, "repos": repos}


def _histogram(sizes, metric="files"):
    if metric == "lines":
        bins = [(0, 50, "≤50"), (50, 100, "51–100"), (100, 200, "101–200"),
                (200, 400, "201–400"), (400, 800, "401–800"), (800, 10**9, ">800")]
    else:
        bins = [(0, 2, "1–2"), (2, 5, "3–5"), (5, 10, "6–10"),
                (10, 20, "11–20"), (20, 40, "21–40"), (40, 10**9, ">40")]
    out = []
    for lo, hi, lbl in bins:
        out.append({"label": lbl, "count": len([s for s in sizes if lo < s <= hi] if lo else [s for s in sizes if s <= hi])})
    return out


def weekly_trend(data, cfg, end, n_weeks=8):
    """每週趨勢：合併 PR 數（拆 AI / 人工）、cycle time 中位數、AI 佔比。"""
    rows = []
    for i in range(n_weeks - 1, -1, -1):
        w_end = end - timedelta(days=7 * i)
        w_start = w_end - timedelta(days=7)
        prs = [x for x in _window_prs(data["pull_requests"], w_start, w_end)
               if not is_bot(x.get("author_name"), cfg) and is_tracked(x, cfg)]
        for x in prs:
            x["_ai"], _ = detect_ai(x, cfg)
        ai_n = len([x for x in prs if x["_ai"]])
        rows.append({
            "week_start": w_start.strftime("%m/%d"),
            "merged": len(prs),
            "ai": ai_n,
            "human": len(prs) - ai_n,
            "ai_pct": pct(ai_n, len(prs)),
            "cycle_p50": p([hours(x.get("created_at"), x.get("merged_at")) for x in prs], 0.5),
        })
    return rows


def compute(data, cfg):
    end = dt(data["now"])
    wd = cfg["window_days"]
    bd = cfg["baseline_days"]
    cur = period(data, cfg, end - timedelta(days=wd), end, f"最近 {wd} 天")
    base = period(data, cfg, end - timedelta(days=wd + bd), end - timedelta(days=wd), f"前 {bd} 天")

    def delta(path):
        a, b = _dig(cur, path), _dig(base, path)
        if a is None or b in (None, 0):
            return None
        return round(100.0 * (a - b) / abs(b), 1)

    deltas = {k: delta(k) for k in [
        "flow.pr_merged", "flow.pr_per_dev_week", "flow.cycle_time_p50", "flow.pr_size_p50",
        "flow.review_coverage_pct", "flow.rework_rate_pct",
        "dora.deploy_freq_per_week", "dora.lead_time_h", "dora.change_failure_rate", "dora.mttr_h",
        "ai.ai_pr_pct", "ai.adoption_pct",
    ]}

    return {
        "meta": {
            "generated_at": data["generated_at"],
            "source": data["source"],
            "team": cfg["team"]["name"],
            "window_days": wd,
            "thresholds": cfg["thresholds"],
            # 來源層若自己做過名單過濾（azdo_source 會），它的報告才看得到「名單外還有誰」，
            # 因為被濾掉的 PR 根本不會進到這裡
            "roster_report": data.get("roster_report") or roster_of(cfg).report(),
        },
        "current": cur,
        "baseline": base,
        "deltas": deltas,
        "weekly": weekly_trend(data, cfg, end, 8),
    }


def _dig(d, path):
    for k in path.split("."):
        if d is None:
            return None
        d = d.get(k)
    return d


def alerts(m):
    """需要人介入的訊號。同一份清單同時給儀表板與文字摘要用。"""
    c, th = m["current"], m["meta"]["thresholds"]
    flow, ai, dora = c["flow"], c["ai"], c["dora"]
    out = []
    if flow["pr_oversize_pct"] > 30:
        out.append(("PR 偏大", f'{flow["pr_oversize_pct"]}% 的 PR 超過 {flow["size_limit"]}'
                               f'{flow["size_unit"]}，大 PR 同時拖慢審查並推高失敗率'))
    if flow["breakdown"]["pickup"] > th["pickup_time_target_hours"]:
        out.append(("等待審查過長", f'PR 平均要等 {flow["breakdown"]["pickup"]:.1f} 小時才有人看，'
                                 f'目標是 {th["pickup_time_target_hours"]} 小時內'))
    if flow["reviewer_concentration_pct"] > 50:
        out.append(("審查負載集中", f'前 2 名審查者承擔 {flow["reviewer_concentration_pct"]}% 的審查，'
                                f'知識與負載都集中在少數人'))
    if flow["stale_pr_count"] > 5:
        out.append(("PR 積壓", f'{flow["stale_pr_count"]} 個 PR 開啟超過 {th["stale_pr_days"]} 天未合併'))
    if flow["review_coverage_pct"] < th["review_coverage_target_pct"]:
        out.append(("審查覆蓋不足", f'只有 {flow["review_coverage_pct"]}% 的 PR 經過審查，'
                                f'目標 {th["review_coverage_target_pct"]}%'))
    a, h = ai.get("ai"), ai.get("human")
    if a and h and a["cfr"] is not None and h["cfr"] is not None and a["cfr"] > h["cfr"] + 2:
        out.append(("AI PR 品質警訊", f'AI 協助 PR 的變更失敗率 {a["cfr"]}%，高於人工 PR 的 {h["cfr"]}%；'
                                  f'先看 PR 大小（{a["size_p50"]} vs {h["size_p50"]}'
                                  f'{flow["size_unit"]}）與審查深度'))
    for key, label in (("change_failure_rate", "變更失敗率"), ("lead_time", "變更前置時間")):
        if dora["ratings"][key] in ("Medium", "Low"):
            out.append((f"DORA {label}落在 {dora['ratings'][key]}", "離 Elite 還有距離，值得排進改善項目"))
    return out


def headline(m):
    """一句話結論。"""
    c, d = m["current"], m["deltas"]
    flow, ai = c["flow"], c["ai"]
    speed = d.get("flow.cycle_time_p50")
    trend = "持平" if speed is None or abs(speed) < 3 else ("變慢" if speed > 0 else "變快")
    return (f'這段期間共合併 <b>{flow["pr_merged"]}</b> 個 PR（{flow["active_devs"]} 位工程師，'
            f'每人每週 <b>{flow["pr_per_dev_week"]}</b> 個），'
            f'PR 週期時間中位數 <b>{fmt_hours(flow["cycle_time_p50"])}</b>，較前期{trend}。'
            f'其中 <b>{ai["ai_pr_pct"]}%</b> 的 PR 有 AI 協助，'
            f'團隊 AI 採用率 <b>{ai["adoption_pct"]}%</b>。')


def fmt_hours(h):
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)} 分"
    if h < 48:
        return f"{h:.1f} 小時"
    return f"{h / 24:.1f} 天"
