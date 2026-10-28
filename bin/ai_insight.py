#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 程式碼品質評估 —— 用 Claude Code CLI 逐人分析產出品質，寫回儀表板。

設計上的三個硬規則（這些不是風格偏好，是讓評分能被拿去當績效參考的前提）：

1. **每個分數都要引用證據。** 模型必須指名是看了哪幾個 PR 給的分。
   沒有引用的分數會被標成「無證據」並排除。一個講不出理由的分數，
   在績效對話裡沒有任何用處 —— 當事人無法反駁，主管也無法說明。

2. **樣本不足就不給分。** PR 數低於門檻只輸出觀察，不輸出分數。
   用 3 個 PR 推斷一個人一季的工作品質，得到的是雜訊不是訊號。

3. **保留原始理由。** 評估結果連同理由一起存檔，可被當事人檢視與申訴。

輸出：out/ai_insight.json
"""
import argparse
import hashlib
import statistics
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import config_loader                      # noqa: E402
import metrics as M                       # noqa: E402

CACHE_DIR = os.path.join(ROOT, "out", ".ai_cache")

# 評分面向。權重刻意讓「可靠度」與「變更設計」不低於「程式碼品質」——
# 寫得漂亮但沒人能審、上線會壞的程式碼，不是高品質產出。
DIMENSIONS = [
    ("code_quality",  "程式碼品質", 0.22, "可讀性、命名、結構、重複程度、錯誤處理"),
    ("change_design", "變更設計",   0.20, "PR 是否切得可審查、單一職責、有沒有把無關改動混進來"),
    ("testing",       "測試落實",   0.18, "有沒有附測試、測試是否涵蓋關鍵路徑而非湊數"),
    ("review_craft",  "審查貢獻",   0.15, "給他人的 review 是否有實質內容、回應 feedback 的方式"),
    ("reliability",   "可靠度",     0.15, "revert／hotfix／核准後回工的頻率"),
    ("impact",        "影響力",     0.10, "處理問題的複雜度與對團隊的價值"),
]
MIN_PRS_FOR_SCORE = 5          # 低於這個數只給觀察，不給分


def _sh(cmd, stdin=None, timeout=600):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)


# ── 階段 1：逐 PR 評估 ───────────────────────────────────────────
# 為什麼要有這一層：只給模型 metadata（檔名、commit 訊息、留言數）時，實測
# 六個面向有三個會被作廢 —— 程式碼品質、測試落實、審查貢獻都需要看到實際的
# 程式碼才評得出來。有本機 clone 時讀真 diff，可信度差一個量級。
PR_PROMPT_VERSION = "2026-10-28.1"
PR_DIMENSIONS = ("code_quality", "change_design", "testing", "reliability")

PR_PROMPT = """你是一位資深工程師，正在審閱一個已經合併的 Pull Request。
這份評估會被聚合成工程師的產出品質參考，當事人有權檢視，所以只講看得到的事實。

## PR
{pr}

## 評分面向（各 1–5 分，5 一律代表好）
- code_quality：可讀性、命名、結構、重複程度、錯誤處理
- change_design：PR 是否切得可審查、單一職責、有沒有混入無關改動
- testing：有沒有附測試、測試是否涵蓋關鍵路徑而非湊數
- reliability：這個改動上線會不會出事（邊界條件、相容性、回滾難度）

分數的意思：1=明顯有問題　2=低於一般水準　3=符合預期　4=穩定優於預期　5=標竿
**3 分是正常值。**

## 硬性要求
1. **沒有 diff 就不要評論程式碼細節。** 看不到的面向把 score 設成 null，
   在 note 寫明缺什麼 —— 猜測比留空有害。
2. observation 要是具體事實（「User.ts 的 null 檢查只做了一半」），
   不要形容詞（「品質不佳」）。
3. 不要因為 PR 小就給低分；重構與維護性工作本身就是有價值的產出。

## 輸出
只輸出 JSON，不要其他文字、不要程式碼區塊：
{{"scores": {{"code_quality": 3, "change_design": 3, "testing": null, "reliability": 3}},
  "observation": "一到兩句，講這個 PR 實際看到什麼",
  "note": "看不到什麼（沒有就空字串）",
  "work_character": "feature|bugfix|refactor|chore|test|docs|mixed"}}"""


def local_diff(pr, cfg):
    """本機有 clone 就讀真 diff。config.ai_insight.repo_paths = {"repo": "/path"}"""
    ai = cfg.get("ai_insight") or {}
    path = (ai.get("repo_paths") or {}).get(pr.get("repo"))
    if not path or not os.path.isdir(path):
        return None
    commits = pr.get("commits") or []
    if not commits:
        return None
    head, base = commits[-1].get("sha"), commits[0].get("sha")
    if not head:
        return None
    limit = ai.get("max_diff_chars", 60000)
    for args in (["diff", "--unified=3", f"{base}~1..{head}"] if base else [],
                 ["show", "--unified=3", "--format=", head]):
        if not args:
            continue
        try:
            r = _sh(["git", "-C", path] + args, timeout=90)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout[:limit]
        except Exception:
            continue
    return None


def _pr_cache_key(pr, has_diff):
    commits = pr.get("commits") or []
    sha = str(commits[-1].get("sha") or "")[:12] if commits else ""
    raw = (f'{PR_PROMPT_VERSION}|{pr.get("repo")}#{pr.get("id")}|{sha}|'
           f'{pr.get("comment_count", 0)}|{"diff" if has_diff else "meta"}')
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def assess_pr(pr, cfg, model=None, no_cache=False):
    """評一個 PR。回傳 (結果, 證據強度, 來源)。"""
    diff = None if (cfg.get("ai_insight") or {}).get("no_diff") else local_diff(pr, cfg)
    level = "diff" if diff else "metadata"
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"pr-{_pr_cache_key(pr, bool(diff))}.json")
    if os.path.exists(cache) and not no_cache:
        try:
            return json.load(open(cache, encoding="utf-8")), level, "cache"
        except (OSError, json.JSONDecodeError):
            pass

    commits = pr.get("commits") or []
    body = {
        "id": pr.get("id"), "repo": pr.get("repo"), "title": pr.get("title"),
        "work_type": pr.get("work_item_type") or "",
        "files_changed": pr.get("files_changed"),
        "additions": pr.get("additions"), "deletions": pr.get("deletions"),
        "commit_messages": [(c.get("message") or "").split("\n")[0][:120]
                            for c in commits][:10],
        "review_comments_received": pr.get("comment_count"),
        "pushes_after_approval": pr.get("pushes_after_approval"),
        "is_revert": bool(pr.get("is_revert")),
    }
    text = json.dumps(body, ensure_ascii=False, indent=1)
    if diff:
        text += "\n\n實際 diff：\n```diff\n" + diff + "\n```"
    else:
        text += "\n\n（沒有 diff —— 這台機器沒有這個 repo 的 clone。"
    prompt = PR_PROMPT.format(pr=text)

    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    r = _sh(cmd, stdin=prompt)
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI 失敗：{r.stderr[:200]}")
    try:
        text_out = (json.loads(r.stdout) or {}).get("result") or ""
    except json.JSONDecodeError:
        text_out = r.stdout
    mm = re.search(r"\{.*\}", text_out, re.S)
    if not mm:
        raise RuntimeError(f"模型沒回 JSON：{text_out[:200]}")
    res = json.loads(mm.group(0))
    json.dump(res, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return res, level, "fresh"


def pr_level(person, mine, cfg, model=None, no_cache=False, limit=8):
    """逐 PR 評估後聚合：每個面向的中位數、依據的 PR、證據強度組成。"""
    picked = sorted(mine, key=lambda x: x.get("merged_at") or "")[-limit:]
    rows, levels = [], {}
    for pr in picked:
        try:
            res, lvl, src = assess_pr(pr, cfg, model=model, no_cache=no_cache)
        except Exception as e:
            print(f"    ⚠️ PR #{pr.get('id')} 評估失敗：{str(e)[:120]}", file=sys.stderr)
            continue
        levels[lvl] = levels.get(lvl, 0) + 1
        sc = {}
        for k in PR_DIMENSIONS:
            v = (res.get("scores") or {}).get(k)
            sc[k] = float(v) if isinstance(v, (int, float)) and 1 <= v <= 5 else None
        rows.append({"pr": pr.get("id"), "title": pr.get("title", ""),
                     "scores": sc, "observation": str(res.get("observation") or "")[:300],
                     "note": str(res.get("note") or "")[:200],
                     "work_character": res.get("work_character"),
                     "evidence_level": lvl, "from": src})
        print(f"    · PR #{pr.get('id')} {str(pr.get('title',''))[:34]}"
              f"　{ {k: v for k, v in sc.items() if v} }　{lvl}/{src}", file=sys.stderr)

    medians, evidence = {}, {}
    for k in PR_DIMENSIONS:
        vals = [(r["scores"][k], r["pr"]) for r in rows if r["scores"].get(k) is not None]
        if len(vals) >= 2:
            medians[k] = round(statistics.median([v for v, _ in vals]), 1)
            # 證據取分數最低與最高的那幾個 PR —— 討論時最有話講
            vals.sort(key=lambda x: x[0])
            picked_ids = [pid for _, pid in vals[:2]] + [pid for _, pid in vals[-2:]]
            evidence[k] = list(dict.fromkeys(picked_ids))
        else:
            medians[k] = None
            evidence[k] = [pid for _, pid in vals]
    return {"assessed": len(rows), "medians": medians, "evidence": evidence,
            "evidence_mix": levels, "prs": rows,
            "prompt_version": PR_PROMPT_VERSION}


def build_packet(person, prs, flow, cfg, prl=None):
    """一個人的證據包。給模型看的全部素材，不多也不少。

    prl 是階段 1（逐 PR，看過真 diff）的結果 —— 有它的話，模型就不是憑
    metadata 猜程式碼品質，而是在整合「已經逐個讀過的結論」。"""
    mine = [p for p in prs if p.get("author") == person["id"]]
    unit = flow.get("size_unit", "個檔")
    items = []
    for p in sorted(mine, key=lambda x: x.get("merged_at") or "")[-40:]:
        items.append({
            "pr": p["id"],
            "repo": p.get("repo"),
            "title": p.get("title"),
            "size": p.get("_size", p.get("files_changed", 0)),
            "files_changed": p.get("files_changed"),
            "commits": len(p.get("commits") or []),
            "commit_messages": [(c.get("message") or "").split("\n")[0][:120]
                                for c in (p.get("commits") or [])][:8],
            "review_comments_received": p.get("comment_count"),
            "reviewers": len(p.get("reviewers") or []),
            "cycle_hours": round(p["_cycle"], 1) if p.get("_cycle") else None,
            "pushes_after_approval": p.get("pushes_after_approval"),
            "ai_assisted": bool(p.get("_ai")),
            "work_type": p.get("work_item_type") or "",
            "is_revert": bool(p.get("is_revert")),
        })
    return {
        "person": {"name": person["name"], "team": person.get("team", "")},
        "window_days": cfg.get("window_days", 28),
        "size_unit": unit,
        "team_baseline": {
            "cycle_p50_hours": flow.get("cycle_time_p50"),
            "pr_size_p50": flow.get("pr_size_p50"),
            "size_limit": flow.get("size_limit"),
            "rework_rate_pct": flow.get("rework_rate_pct"),
        },
        "their_totals": {
            "merged_prs": person.get("pr_merged"),
            "reviews_given": person.get("reviews_given"),
            "cycle_p50_hours": person.get("cycle_p50"),
            "size_p50": person.get("size_p50"),
            "rework_pct": person.get("rework_pct"),
            "ai_assisted_pct": person.get("ai_pct"),
        },
        "pull_requests": items,
        "pr_level_review": ({
            "說明": "以下是逐個 PR 讀過之後的結論（有 diff 的部分可信度高）。"
                    "整合時以這些觀察為主，不要另外臆測程式碼細節。",
            "已評估 PR 數": prl["assessed"],
            "證據強度組成": prl["evidence_mix"],
            "各面向中位數": prl["medians"],
            "逐 PR 觀察": [{"pr": r["pr"], "scores": r["scores"],
                            "observation": r["observation"], "note": r["note"],
                            "evidence_level": r["evidence_level"]} for r in prl["prs"]],
        } if prl and prl.get("assessed") else None),
    }


PROMPT = """你是一位資深工程主管，要為一位工程師寫一份**有證據的產出品質評估**。
這份評估會被當成績效對話的參考資料，當事人有權看到並提出異議，所以每個判斷都必須站得住腳。

## 你拿到的資料
{packet}

## 評分面向（各自 1–5 分）
{dims}

分數的意思：1=明顯有問題　2=低於團隊水準　3=符合預期　4=穩定優於預期　5=標竿
**3 分是正常值。** 多數人在多數面向上就是 3 分，不要為了區分而灌分或壓分。

## 硬性要求
1. 每個面向的 `evidence` 必須列出你實際依據的 PR 編號（至少 1 個）。
   舉不出 PR 就把該面向的 score 設成 null、reason 寫「資料不足以判斷」。
2. 只根據上面的資料下判斷。**不要臆測**資料裡沒有的東西
   （例如沒有 diff 就不要評論變數命名；沒有測試檔資訊就不要斷言他不寫測試）。
3. `reason` 用具體事實，不要形容詞堆砌。
   寫「PR #4021 一次改 38 個檔並混入無關的格式調整」，
   不要寫「變更設計品質有待加強」。
4. 如果資料本身不足以支撐一份評估，把 `insufficient_data` 設成 true 並說明缺什麼。
5. `coaching` 是寫給主管看的：下一次 1-on-1 該談什麼、該提供什麼協助。

## 輸出
只輸出 JSON，不要有其他文字、不要包在程式碼區塊裡：
{{
  "insufficient_data": false,
  "summary": "兩到三句話的整體描述，講事實",
  "scores": {{
    "code_quality":  {{"score": 3, "reason": "...", "evidence": [4021, 4035]}},
    "change_design": {{"score": null, "reason": "資料不足以判斷", "evidence": []}},
    "testing":       {{"score": 3, "reason": "...", "evidence": [4040]}},
    "review_craft":  {{"score": 4, "reason": "...", "evidence": [4012]}},
    "reliability":   {{"score": 3, "reason": "...", "evidence": [4055]}},
    "impact":        {{"score": 3, "reason": "...", "evidence": [4021]}}
  }},
  "strengths": ["具體的、有 PR 佐證的優點"],
  "growth_areas": ["具體的、有 PR 佐證的待改進處"],
  "coaching": ["主管下次 1-on-1 可以做的具體動作"],
  "data_gaps": ["這份評估沒看到、但會影響判斷的東西"]
}}"""


def assess(packet, model=None, no_cache=False):
    dims = "\n".join(f"- **{zh}**（{key}）：{desc}" for key, zh, _w, desc in DIMENSIONS)
    prompt = PROMPT.format(packet=json.dumps(packet, ensure_ascii=False, indent=1), dims=dims)

    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(prompt.encode()).hexdigest()[:20]
    cache = os.path.join(CACHE_DIR, f"{key}.json")
    if os.path.exists(cache) and not no_cache:
        return json.load(open(cache, encoding="utf-8")), "cache"

    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    r = _sh(cmd, stdin=prompt)
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI 失敗：{r.stderr[:300]}")
    try:
        envelope = json.loads(r.stdout)
        text = envelope.get("result") or ""
    except json.JSONDecodeError:
        text = r.stdout
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"模型沒有回傳 JSON：{text[:300]}")
    data = json.loads(m.group(0))
    json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return data, "fresh"


def _validate(result, valid_prs):
    """把講不出證據的分數作廢。這是整份評估可信度的關鍵一步。"""
    scores, dropped = {}, []
    for key, zh, _w, _d in DIMENSIONS:
        item = (result.get("scores") or {}).get(key) or {}
        s, ev = item.get("score"), [e for e in (item.get("evidence") or []) if e in valid_prs]
        if s is None or not ev:
            dropped.append(zh)
            scores[key] = {"score": None, "reason": item.get("reason") or "資料不足以判斷",
                           "evidence": ev, "dropped": True}
            continue
        scores[key] = {"score": max(1, min(5, int(s))), "reason": item.get("reason", ""),
                       "evidence": ev[:6], "dropped": False}
    return scores, dropped


def zh_of(key):
    for k, zh, _w, _d in DIMENSIONS:
        if k == key:
            return zh
    return key


def overall(scores):
    """加權總分，只用有證據的面向，並回報涵蓋率。"""
    num = den = 0.0
    for key, _zh, w, _d in DIMENSIONS:
        s = scores[key]["score"]
        if s is not None:
            num += s * w
            den += w
    if den == 0:
        return None, 0.0
    return round(num / den, 2), round(den, 2)


def run(cfg, m, data, only=None, model=None, no_cache=False, pr_limit=None):
    flow = m["current"]["flow"]
    # 證據包只能放「觀測窗內」的 PR —— 放進基準期的 PR 會讓明細筆數
    # 和彙總數字對不上，模型會（正確地）判定資料不可信而拒絕評分
    # start/end 是 +00:00 格式、merged_at 是 Z 格式，字串比不得，一律轉 datetime
    w_start, w_end = M.dt(m["current"]["start"]), M.dt(m["current"]["end"])
    out = {"generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
           "window_days": cfg.get("window_days", 28),
           "min_prs_for_score": MIN_PRS_FOR_SCORE,
           "dimensions": [{"key": k, "label": z, "weight": w, "desc": d}
                          for k, z, w, d in DIMENSIONS],
           "people": []}

    merged = [p for p in data["pull_requests"]
              if p.get("merged_at") and w_start <= M.dt(p["merged_at"]) < w_end]
    for person in m["current"]["people"]:
        if only and person["name"] not in only and person["id"] not in only:
            continue
        n = person.get("pr_merged") or 0
        row = {"id": person["id"], "name": person["name"], "team": person.get("team", ""),
               "pr_count": n}
        if n < MIN_PRS_FOR_SCORE:
            row.update({"scored": False,
                        "note": f"觀測窗內只有 {n} 個合併 PR，樣本不足以評分"
                                f"（門檻 {MIN_PRS_FOR_SCORE}）。這不代表產出不好，"
                                f"只代表這段期間的資料撐不起一個分數。"})
            out["people"].append(row)
            print(f"  · {person['name']}：PR {n} 個 → 樣本不足，略過評分", file=sys.stderr)
            continue

        mine = [p for p in merged if p.get("author") == person["id"]]
        ai_cfg = cfg.get("ai_insight") or {}
        lim = pr_limit if pr_limit is not None else ai_cfg.get("pr_limit", 8)
        prl = None
        if lim > 0:
            print(f"  · {person['name']}：逐 PR 評估（最多 {lim} 個）…", file=sys.stderr)
            prl = pr_level(person, mine, cfg, model=model, no_cache=no_cache, limit=lim)
        packet = build_packet(person, merged, flow, cfg, prl=prl)
        valid = {it["pr"] for it in packet["pull_requests"]}
        try:
            res, src = assess(packet, model=model, no_cache=no_cache)
        except Exception as e:
            row.update({"scored": False, "note": f"評估失敗：{e}"})
            out["people"].append(row)
            print(f"  · {person['name']}：失敗 — {e}", file=sys.stderr)
            continue

        scores, dropped = _validate(res, valid)
        # 程式碼相關的四個面向，分數改用「逐 PR 中位數」—— 那是一個一個讀出來的，
        # 可追溯；模型在人層級給的分數只留 reason 與證據 PR。
        if prl:
            for k in PR_DIMENSIONS:
                med = prl["medians"].get(k)
                if med is None:
                    continue
                ev = [e for e in (prl["evidence"].get(k) or []) if e in valid]
                cur_reason = (scores.get(k) or {}).get("reason") or ""
                scores[k] = {
                    "score": round(med),
                    "reason": (cur_reason or "逐 PR 評估的中位數")
                              + f"（逐 PR 中位數 {med}，{prl['assessed']} 個 PR）",
                    "evidence": ev[:6] or ((scores.get(k) or {}).get("evidence") or [])[:6],
                    "dropped": False,
                }
                if zh_of(k) in dropped:
                    dropped.remove(zh_of(k))
        score, coverage = overall(scores)
        row.update({
            "scored": score is not None,
            "overall": score,
            "coverage": coverage,
            "evidence_prs": sorted({e for s in scores.values() for e in s["evidence"]}),
            "summary": res.get("summary", ""),
            "scores": scores,
            "dropped_dimensions": dropped,
            "strengths": res.get("strengths", [])[:4],
            "growth_areas": res.get("growth_areas", [])[:4],
            "coaching": res.get("coaching", [])[:4],
            "data_gaps": res.get("data_gaps", [])[:4],
            "source": src,
            # 可追溯：這些分數是從哪幾個 PR 讀出來的、有沒有真 diff
            "pr_level": ({"assessed": prl["assessed"], "medians": prl["medians"],
                          "evidence_mix": prl["evidence_mix"],
                          "prompt_version": prl["prompt_version"],
                          "prs": prl["prs"]} if prl else None),
        })
        out["people"].append(row)
        print(f"  · {person['name']}：{score if score else '—'}"
              f"（涵蓋 {coverage:.0%}，{src}）", file=sys.stderr)

    scored = [p for p in out["people"] if p.get("scored")]
    out["team_summary"] = {
        "assessed": len(scored),
        "skipped": len(out["people"]) - len(scored),
        "median": round(sorted(p["overall"] for p in scored)[len(scored) // 2], 2) if scored else None,
        "mean": round(sum(p["overall"] for p in scored) / len(scored), 2) if scored else None,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description="AI 程式碼品質評估")
    ap.add_argument("--only", nargs="*", help="只評估這幾位（名字）")
    ap.add_argument("--model", help="指定模型，預設用 CLI 的設定")
    ap.add_argument("--no-cache", action="store_true", help="忽略快取重跑")
    ap.add_argument("--pr-limit", type=int,
                    help="每人最多逐個評幾個 PR（預設 8，0＝跳過逐 PR 只做人層級）")
    ap.add_argument("--no-diff", action="store_true",
                    help="不讀本機 clone 的 diff（只用 metadata，便宜但評不出程式碼品質）")
    ap.add_argument("--source", choices=["azdo", "demo", "auto"])
    a = ap.parse_args()

    cfg = config_loader.load()
    sys.path.insert(0, os.path.join(ROOT, "bin"))
    from dashboard import get_data
    data = get_data(cfg, a.source)
    m = M.compute(data, cfg)

    print(f"🤖 AI 評估：{len(m['current']['people'])} 位成員，"
          f"觀測窗 {cfg.get('window_days', 28)} 天", file=sys.stderr)
    if a.no_diff:
        cfg.setdefault("ai_insight", {})["no_diff"] = True
    out = run(cfg, m, data, only=a.only, model=a.model, no_cache=a.no_cache,
              pr_limit=a.pr_limit)

    path = os.path.join(ROOT, "out", "ai_insight.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n✅ {path}", file=sys.stderr)
    ts = out["team_summary"]
    print(f"   評分 {ts['assessed']} 位 / 樣本不足 {ts['skipped']} 位"
          f"｜中位數 {ts['median']}", file=sys.stderr)
    mix = {}
    for pp in out["people"]:
        for k, v in ((pp.get("pr_level") or {}).get("evidence_mix") or {}).items():
            mix[k] = mix.get(k, 0) + v
    if mix:
        print(f"   逐 PR 證據組成：{mix}"
              + ("　⚠️ 全部只有 metadata，程式碼品質類分數可信度低；"
                 "設定 ai_insight.repo_paths 指到本機 clone 才讀得到 diff"
                 if mix.get("diff", 0) == 0 else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
