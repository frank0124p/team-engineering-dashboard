#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Insight — 用本機 Claude Code 逐個 PR 做程式碼品質評估，聚合成個人視圖。

用法：
  /usr/bin/python3 bin/ai_review.py                  # 評觀測窗內還沒評過的 PR
  /usr/bin/python3 bin/ai_review.py --limit 20       # 這次最多評 20 個（控成本）
  /usr/bin/python3 bin/ai_review.py --source demo    # 用示範資料跑通管線
  /usr/bin/python3 bin/ai_review.py --person "Amy Chen"
  /usr/bin/python3 bin/ai_review.py --rescore        # 忽略快取重評
  /usr/bin/python3 bin/ai_review.py --dry-run        # 只印會送出什麼，不呼叫 Claude

產物：
  out/ai-insight.json     個人／團隊聚合結果（dashboard.py 會讀它）
  out/ai-reviews.json     每個 PR 的逐筆評估與證據（快取，可追溯）

── 為什麼這支程式長這樣 ──────────────────────────────────────────
這份輸出會被用來當績效考核的參考，所以每一個設計都在對抗「分數看起來很客觀」
這件事造成的誤判：

1. **證據強度要跟著分數一起走**。有本機 clone 能讀到真 diff 的 PR，和只有
   檔名＋commit message 的 PR，可信度差一個量級 —— 兩者的 evidence_level 不同，
   聚合時會分開統計，頁面上也會標出來。
2. **樣本數不夠就不給分**。預設至少 5 個 PR 才顯示個人分數（min_samples），
   不然就是「資料不足」。3 個 PR 的中位數不是訊號，是雜訊。
3. **分數是 1–5 且全部「5 = 好」**，包含風險類指標（correctness 5 分代表風險低），
   避免看表的人要記哪個維度是反向的。
4. **同一個 PR 的評分會快取並記錄 prompt 版本與模型**。換模型或改 prompt 之後，
   舊分數不能直接跟新分數比 —— 聚合結果會把混用的情況標出來。
5. **不做分數排名榜**。輸出按 PR 數排序，要按分數排自己去排 —— 這是刻意的摩擦。
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import config_loader                           # noqa: E402

PROMPT_VERSION = "2026-10-28.1"
REVIEWS_PATH = os.path.join(ROOT, "out", "ai-reviews.json")
INSIGHT_PATH = os.path.join(ROOT, "out", "ai-insight.json")

DIMENSIONS = {
    "correctness": "正確性風險（5＝看不出明顯風險；1＝很可能有 bug 或漏處理）",
    "tests": "測試（5＝變更都有對應測試；1＝改了邏輯卻完全沒測試）",
    "readability": "可讀性與可維護性（5＝命名清楚、結構合理；1＝難讀、重複、魔術數字）",
    "scope": "範圍聚焦（5＝一個 PR 做一件事，好審；1＝混雜多個不相關的改動）",
    "responsiveness": "審查回饋處理（5＝意見被確實處理；1＝忽略或草率帶過）",
}

SCHEMA_HINT = """只回傳一個 JSON 物件，不要任何說明文字或 markdown 圍欄：
{
  "scores": {"correctness": 1-5, "tests": 1-5, "readability": 1-5, "scope": 1-5, "responsiveness": 1-5},
  "overall": 1-5,
  "confidence": "high" | "medium" | "low",
  "evidence": ["引用具體檔名／行為，最多 4 條，每條 40 字內"],
  "risks": ["具體風險，最多 3 條，沒有就空陣列"],
  "suggestions": ["可執行的建議，最多 3 條"],
  "work_character": "feature" | "bugfix" | "refactor" | "chore" | "test" | "docs" | "mixed"
}
規則：
- 每個維度都用 1-5，5 一律代表「好」（correctness 給 5 是指看不出風險）。
- 資料不足以判斷某個維度時，給 3 並把 confidence 設成 low，在 evidence 說明看不到什麼。
- 不要猜測作者的能力或態度，只描述這個 PR 本身觀察到的事實。
- 不要因為 PR 小就給低分；維護性工作與重構本身就是有價值的產出。"""


# ── 讀寫快取 ─────────────────────────────────────────────────────
def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def pr_fingerprint(pr):
    """PR 內容指紋：變了就要重評。"""
    last_sha = ""
    commits = pr.get("commits") or []
    if commits:
        last_sha = str(commits[-1].get("sha") or "")[:12]
    return (f'{pr.get("repo")}#{pr.get("id")}:{last_sha}:'
            f'{pr.get("comment_count", 0)}:{pr.get("pushes_after_approval", 0)}')


# ── 取素材 ───────────────────────────────────────────────────────
def local_diff(pr, cfg):
    """本機有 clone 就讀真 diff —— 證據強度差一個量級。

    config.ai_insight.repo_paths = {"repo-name": "/path/to/clone"}
    """
    paths = (cfg.get("ai_insight") or {}).get("repo_paths") or {}
    path = paths.get(pr.get("repo"))
    if not path or not os.path.isdir(path):
        return None
    commits = pr.get("commits") or []
    if not commits:
        return None
    head = commits[-1].get("sha")
    base = commits[0].get("sha")
    if not head or not base:
        return None
    max_chars = (cfg.get("ai_insight") or {}).get("max_diff_chars", 60000)
    try:
        r = subprocess.run(["git", "-C", path, "diff", "--unified=3", f"{base}~1..{head}"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not r.stdout.strip():
            r = subprocess.run(["git", "-C", path, "show", "--unified=3", head],
                               capture_output=True, text=True, timeout=60)
        out = r.stdout
        return out[:max_chars] if out.strip() else None
    except Exception:
        return None


def build_prompt(pr, cfg, diff=None):
    commits = pr.get("commits") or []
    msgs = [str(c.get("message") or "").strip().splitlines()[0][:120]
            for c in commits if c.get("message")]
    size_bits = []
    if pr.get("files_changed"):
        size_bits.append(f'{pr["files_changed"]} 個檔')
    if pr.get("additions") or pr.get("deletions"):
        size_bits.append(f'+{pr.get("additions", 0)}/-{pr.get("deletions", 0)} 行')

    L = [
        "你是一位資深工程師，正在對一個已合併的 Pull Request 做品質評估。",
        "這份評估會成為團隊討論與個人成長的素材，所以必須有憑有據、不臆測。",
        "",
        "## PR 基本資料",
        f'標題：{pr.get("title", "")}',
        f'Repo：{pr.get("repo", "")}　PR #{pr.get("id", "")}',
        f'類型標記：{pr.get("work_item_type") or "（無）"}',
        f'規模：{"、".join(size_bits) or "（不明）"}',
        f'審查者：{", ".join(pr.get("reviewers") or []) or "（無）"}'
        f'　留言數：{pr.get("comment_count", 0)}'
        f'　核准後又推了 {pr.get("pushes_after_approval", 0)} 次',
    ]
    if pr.get("description"):
        L += ["", "## PR 描述", str(pr["description"])[:2000]]
    if msgs:
        L += ["", "## Commit 訊息"] + [f"- {m}" for m in msgs[:20]]
    if pr.get("file_paths"):
        L += ["", "## 變更檔案"] + [f"- {p}" for p in pr["file_paths"][:40]]
    if pr.get("review_comments"):
        L += ["", "## 審查留言（人寫的）"]
        for cm in pr["review_comments"][:15]:
            L.append(f'- {str(cm)[:200]}')
    if diff:
        L += ["", "## 實際 diff", "```diff", diff, "```"]
        evidence_level = "diff"
    else:
        L += ["", "（**沒有 diff**：只有上面這些 metadata。看不到的面向請給 3 分並把 "
              "confidence 設成 low，不要用猜測補足。）"]
        evidence_level = "metadata"

    L += ["", "## 評分維度"]
    L += [f"- {k}：{v}" for k, v in DIMENSIONS.items()]
    L += ["", SCHEMA_HINT]
    return "\n".join(L), evidence_level


# ── 呼叫 Claude Code ─────────────────────────────────────────────
def call_claude(prompt, cfg, timeout=300):
    ai = cfg.get("ai_insight") or {}
    model = ai.get("model", "sonnet")
    cmd = [ai.get("cli", "claude"), "-p", prompt,
           "--output-format", "json",
           "--model", model,
           "--no-session-persistence"]
    # 這是純文字推理，不需要任何工具；關掉可避免它去讀專案檔案或亂跑指令
    cmd += ["--allowedTools", ""]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=ROOT, env={**os.environ, "CLAUDE_CODE_DISABLE_HOOKS": "1"})
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI 失敗（rc={r.returncode}）：{(r.stderr or '')[:300]}")
    raw = r.stdout.strip()
    try:
        env = json.loads(raw)
        text = env.get("result") if isinstance(env, dict) else raw
    except json.JSONDecodeError:
        text = raw
    return _parse_json(text), model


def _parse_json(text):
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _sanitize(res):
    """把模型回來的東西收斂成固定形狀，壞掉的維度補 None 而不是亂猜。"""
    out = {"scores": {}, "overall": None, "confidence": "low",
           "evidence": [], "risks": [], "suggestions": [], "work_character": None}
    sc = (res or {}).get("scores") or {}
    for k in DIMENSIONS:
        v = sc.get(k)
        if isinstance(v, (int, float)) and 1 <= v <= 5:
            out["scores"][k] = round(float(v), 1)
        else:
            out["scores"][k] = None
    ov = (res or {}).get("overall")
    if isinstance(ov, (int, float)) and 1 <= ov <= 5:
        out["overall"] = round(float(ov), 1)
    else:
        vals = [v for v in out["scores"].values() if v is not None]
        out["overall"] = round(statistics.mean(vals), 1) if vals else None
    cf = str((res or {}).get("confidence") or "low").lower()
    out["confidence"] = cf if cf in ("high", "medium", "low") else "low"
    for k in ("evidence", "risks", "suggestions"):
        v = (res or {}).get(k) or []
        out[k] = [str(x)[:200] for x in v][:4] if isinstance(v, list) else []
    wc = (res or {}).get("work_character")
    out["work_character"] = str(wc)[:20] if wc else None
    return out


# ── 聚合 ─────────────────────────────────────────────────────────
def _median(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 2) if vals else None


def aggregate(reviews, cfg, window_prs):
    ai = cfg.get("ai_insight") or {}
    min_samples = ai.get("min_samples", 5)
    by_person = {}
    in_window = {f'{p.get("repo")}#{p.get("id")}': p for p in window_prs}

    for key, rv in reviews.items():
        pr_key = key.split(":")[0]
        pr = in_window.get(pr_key)
        if not pr:
            continue
        who = pr.get("author_name") or pr.get("author") or "?"
        b = by_person.setdefault(who, {"name": who, "prs": [], "models": set(),
                                       "prompt_versions": set(), "evidence": {}})
        b["prs"].append({**rv, "pr": pr_key, "title": pr.get("title", ""),
                         "repo": pr.get("repo", "")})
        b["models"].add(rv.get("model") or "?")
        b["prompt_versions"].add(rv.get("prompt_version") or "?")
        lvl = rv.get("evidence_level") or "metadata"
        b["evidence"][lvl] = b["evidence"].get(lvl, 0) + 1

    people = []
    for who, b in by_person.items():
        n = len(b["prs"])
        dims = {k: _median([p["scores"].get(k) for p in b["prs"]]) for k in DIMENSIONS}
        overall = _median([p.get("overall") for p in b["prs"]])
        conf = {c: sum(1 for p in b["prs"] if p.get("confidence") == c)
                for c in ("high", "medium", "low")}
        risks = {}
        for p in b["prs"]:
            for r in p.get("risks") or []:
                risks[r[:60]] = risks.get(r[:60], 0) + 1
        enough = n >= min_samples
        people.append({
            "name": who,
            "reviewed_prs": n,
            "enough_samples": enough,
            "scores": dims if enough else None,
            "overall": overall if enough else None,
            "status": "ok" if enough else f"資料不足（{n}/{min_samples} 個 PR）",
            "confidence_mix": conf,
            "evidence_mix": b["evidence"],
            "mixed_runs": len(b["models"]) > 1 or len(b["prompt_versions"]) > 1,
            "models": sorted(b["models"]),
            "top_risks": sorted(risks.items(), key=lambda x: -x[1])[:3],
            "samples": sorted(b["prs"], key=lambda p: (p.get("overall") or 0))[:3],
        })
    # 刻意按 PR 數排序，不按分數 —— 不要做成排名榜
    people.sort(key=lambda z: -z["reviewed_prs"])

    scored = [p for p in people if p["overall"] is not None]
    team = {
        "reviewed_prs": sum(p["reviewed_prs"] for p in people),
        "people_scored": len(scored),
        "people_insufficient": len(people) - len(scored),
        "overall_median": _median([p["overall"] for p in scored]),
        "dimension_median": {k: _median([(p["scores"] or {}).get(k) for p in scored])
                             for k in DIMENSIONS},
        "evidence_mix": {},
        "min_samples": min_samples,
    }
    for p in people:
        for k, v in p["evidence_mix"].items():
            team["evidence_mix"][k] = team["evidence_mix"].get(k, 0) + v
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt_version": PROMPT_VERSION,
        "dimensions": DIMENSIONS,
        "team": team,
        "people": people,
        "usage_guardrails": [
            "這是對話素材，不是績效分數。要用在考核上，必須有人逐筆看過 evidence 再下判斷。",
            f"樣本數少於 {team['min_samples']} 個 PR 的人不給分（status 會寫資料不足）。",
            "evidence_mix 標 metadata 的部分代表沒有真 diff，可信度明顯較低。",
            "分數不可跨 prompt_version／model 直接比較（mixed_runs 為 true 者尤其）。",
            "重構、修 bug、維護性工作本身就是產出，不要因為沒有新功能就當成低產能。",
        ],
    }


# ── 主流程 ───────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--source", choices=["azdo", "demo", "auto"])
    ap.add_argument("--window", type=int)
    ap.add_argument("--limit", type=int, default=25, help="這次最多評幾個 PR（控成本）")
    ap.add_argument("--person", help="只評某個人的 PR")
    ap.add_argument("--rescore", action="store_true", help="忽略快取重評")
    ap.add_argument("--dry-run", action="store_true", help="只印第一個 prompt，不呼叫 Claude")
    a = ap.parse_args()

    cfg = config_loader.load(a.config, {"window_days": a.window})
    ai_cfg = cfg.setdefault("ai_insight", {})

    sys.path.insert(0, os.path.join(ROOT, "bin"))
    from dashboard import get_data              # 沿用同一套來源選擇邏輯
    import metrics

    data = get_data(cfg, a.source)
    end = metrics.dt(data["now"])
    from datetime import timedelta
    start = end - timedelta(days=cfg["window_days"])
    prs = [x for x in data["pull_requests"]
           if x.get("merged_at") and start <= metrics.dt(x["merged_at"]) < end
           and not metrics.is_bot(x.get("author_name"), cfg)
           and metrics.is_tracked(x, cfg)]
    if a.person:
        key = config_loader.norm_person(a.person)
        prs = [x for x in prs
               if key in config_loader.person_keys(x.get("author"), x.get("author_name"))]

    reviews = {} if a.rescore else _load(REVIEWS_PATH, {})
    todo = [p for p in prs if pr_fingerprint(p) not in reviews]
    print(f"觀測窗內 {len(prs)} 個 PR，已評 {len(prs) - len(todo)} 個，"
          f"這次要評 {min(len(todo), a.limit)} 個（--limit {a.limit}）", file=sys.stderr)

    if a.dry_run:
        if todo:
            pr = todo[0]
            prompt, lvl = build_prompt(pr, cfg, local_diff(pr, cfg))
            print(f"--- 證據強度：{lvl} ---\n{prompt}")
        return 0

    done = 0
    for pr in todo[:a.limit]:
        fp = pr_fingerprint(pr)
        diff = local_diff(pr, cfg)
        prompt, lvl = build_prompt(pr, cfg, diff)
        try:
            raw, model = call_claude(prompt, cfg)
            rv = _sanitize(raw)
        except Exception as e:
            print(f"  ⚠️ PR {pr.get('repo')}#{pr.get('id')} 評估失敗：{str(e)[:160]}",
                  file=sys.stderr)
            continue
        rv.update({"model": model, "prompt_version": PROMPT_VERSION,
                   "evidence_level": lvl,
                   "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        reviews[fp] = rv
        done += 1
        print(f"  ✅ {pr.get('repo')}#{pr.get('id')} {pr.get('title', '')[:40]}"
              f"　overall {rv['overall']}　{rv['confidence']}／{lvl}", file=sys.stderr)
        _save(REVIEWS_PATH, reviews)        # 邊跑邊存，中斷不會白跑

    insight = aggregate(reviews, cfg, prs)
    _save(INSIGHT_PATH, insight)
    t = insight["team"]
    print(f"\n本次新評 {done} 個 PR。累計 {t['reviewed_prs']} 筆進入聚合："
          f"{t['people_scored']} 人有分數、{t['people_insufficient']} 人資料不足")
    print(f"團隊 overall 中位 {t['overall_median']}　"
          f"證據組成 {t['evidence_mix']}")
    print(f"INSIGHT: {INSIGHT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
