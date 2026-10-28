"""示範資料產生器 — 產生結構與 Azure DevOps adapter 完全相同的正規化資料。

目的：在還沒拿到公司 Azure DevOps 權限前，讓整條管線（抓取→指標→渲染）
可以端到端跑起來。換成真實來源時只需把 config.source 改成 "azdo"。
"""
import random
from datetime import datetime, timedelta, timezone

PEOPLE = [
    ("amy.chen",   "Amy Chen",     "Backend",  0.85),   # (id, 顯示名, 小組, AI 採用傾向)
    ("ben.liu",    "Ben Liu",      "Backend",  0.55),
    ("carol.wu",   "Carol Wu",     "Frontend", 0.90),
    ("david.lin",  "David Lin",    "Frontend", 0.20),
    ("ellen.kuo",  "Ellen Kuo",    "Platform", 0.70),
    ("frank.yeh",  "Frank Yeh",    "Platform", 0.95),
    ("grace.tsai", "Grace Tsai",   "Data",     0.45),
    ("henry.ho",   "Henry Ho",     "Data",     0.10),
]
REPOS = ["core-api", "web-portal", "data-pipeline"]
WORK_TYPES = ["Feature", "Bug", "Tech Debt", "Chore"]
TITLES = [
    "Add retry policy to {}", "Fix null handling in {}", "Refactor {} module",
    "Bump dependencies for {}", "Introduce caching layer in {}",
    "Harden input validation in {}", "Migrate {} to new schema",
    "Improve {} error messages", "Add integration tests for {}",
    "Optimise {} query path",
]
SUBJECTS = ["auth", "billing", "search", "ingest", "scheduler", "export", "webhook", "cache"]


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch(cfg, now=None):
    rnd = random.Random(20261027)
    now = now or datetime.now(timezone.utc)
    # 多產一段基準期，讓「vs 前一期」可以比較
    total_days = cfg.get("window_days", 28) + cfg.get("baseline_days", 28)
    start = now - timedelta(days=total_days)

    prs, deployments, incidents = [], [], []
    pr_id, dep_id = 4000, 9000

    for day in range(total_days):
        d0 = start + timedelta(days=day)
        if d0.weekday() >= 5:                      # 週末產出大幅降低
            daily = rnd.choices([0, 1, 2], [0.72, 0.2, 0.08])[0]
        else:
            # 後半期（近 28 天）AI 工具普及，吞吐量略增
            ramp = 1.0 + 0.35 * (day / total_days)
            daily = max(0, int(rnd.gauss(6.5 * ramp, 2.2)))

        for _ in range(daily):
            pid, name, team, ai_bias = rnd.choice(PEOPLE)
            repo = rnd.choices(REPOS, [0.45, 0.35, 0.20])[0]
            # AI 採用率隨時間上升
            adoption_ramp = 0.35 + 0.65 * (day / total_days)
            ai = rnd.random() < ai_bias * adoption_ramp
            tool = None
            if ai:
                tool = rnd.choices(["Claude Code", "GitHub Copilot", "Cursor"], [0.55, 0.3, 0.15])[0]

            first_commit = d0 + timedelta(hours=rnd.uniform(1, 9))
            # AI 協助 → coding time 較短，但 PR 偏大
            coding_h = abs(rnd.gauss(6 if ai else 11, 4))
            opened = first_commit + timedelta(hours=coding_h)

            size_mu = 230 if ai else 160
            additions = max(3, int(abs(rnd.gauss(size_mu, size_mu * 0.8))))
            deletions = max(0, int(additions * abs(rnd.gauss(0.35, 0.3))))
            files = max(1, int((additions + deletions) / rnd.uniform(25, 90)))

            reviewers = [p[0] for p in rnd.sample(PEOPLE, rnd.choices([1, 2, 3], [0.55, 0.35, 0.10])[0])
                         if p[0] != pid]
            reviewed = bool(reviewers) and rnd.random() < 0.94

            pickup_h = abs(rnd.gauss(14, 12)) + (6 if additions > 400 else 0)
            review_h = abs(rnd.gauss(9 if ai else 7, 7)) + (additions / 120.0)
            idle_h = abs(rnd.gauss(3, 3))

            first_review = opened + timedelta(hours=pickup_h) if reviewed else None
            approved = first_review + timedelta(hours=review_h) if reviewed else None
            status = rnd.choices(["completed", "active", "abandoned"], [0.88, 0.08, 0.04])[0]
            if day > total_days - 3 and status == "completed":
                status = rnd.choices(["completed", "active"], [0.5, 0.5])[0]

            merged = (approved + timedelta(hours=idle_h)) if (approved and status == "completed") else None
            if merged and merged > now:
                merged, status = None, "active"

            n_commits = max(1, int(abs(rnd.gauss(4 if ai else 3, 2))))
            commits = []
            for c in range(n_commits):
                msg = rnd.choice(TITLES).format(rnd.choice(SUBJECTS))
                if ai and c == 0:
                    msg += f"\n\n🤖 Generated with {tool}\n\nCo-Authored-By: {tool} <noreply@example.com>"
                commits.append({
                    "sha": f"{rnd.getrandbits(80):020x}",
                    "author": pid,
                    "authored_at": _iso(first_commit + timedelta(hours=c * coding_h / n_commits)),
                    "message": msg,
                })

            pid_num = pr_id
            prs.append({
                "id": pid_num,
                "repo": repo,
                "title": rnd.choice(TITLES).format(rnd.choice(SUBJECTS)),
                "author": pid,
                "author_name": name,
                "team": team,
                "first_commit_at": _iso(first_commit),
                "created_at": _iso(opened),
                "first_review_at": _iso(first_review) if first_review else None,
                "first_approval_at": _iso(approved) if approved else None,
                "merged_at": _iso(merged) if merged else None,
                "closed_at": _iso(merged) if merged else None,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "files_changed": files,
                "reviewers": reviewers,
                "comment_count": max(0, int(abs(rnd.gauss(5 if ai else 4, 4)))),
                "pushes_after_approval": rnd.choices([0, 1, 2], [0.72, 0.22, 0.06])[0],
                "work_item_type": rnd.choices(WORK_TYPES, [0.46, 0.27, 0.17, 0.10])[0],
                "target_branch": "main",
                "labels": (["ai-assisted"] if ai and rnd.random() < 0.3 else []),
                "commits": commits,
                "is_revert": False,
            })
            pr_id += 1

            if merged:
                dep_time = merged + timedelta(hours=abs(rnd.gauss(2.5, 2)))
                if dep_time <= now:
                    # AI PR 若過大，失敗率略升 — 這正是護欄指標要抓的訊號
                    fail_p = 0.045 + (0.035 if (ai and additions > 400) else 0)
                    failed = rnd.random() < fail_p
                    deployments.append({
                        "id": dep_id, "repo": repo, "pipeline": f"{repo}-release",
                        "environment": "Production",
                        "finished_at": _iso(dep_time),
                        "result": "failed" if failed else "succeeded",
                        "pr_id": pid_num, "commit_sha": commits[-1]["sha"],
                    })
                    if failed:
                        incidents.append({
                            "id": f"INC-{dep_id}", "repo": repo,
                            "deployment_id": dep_id,
                            "started_at": _iso(dep_time),
                            "resolved_at": _iso(dep_time + timedelta(hours=abs(rnd.gauss(2.2, 1.8)) + 0.3)),
                        })
                    dep_id += 1

    return {
        "source": "mock",
        "size_metric": cfg.get("pr_size_metric", "files"),
        "scope": {"organization": "（示範）", "project": "（示範）"},
        "generated_at": _iso(now),
        "now": _iso(now),
        "people": [{"id": p[0], "name": p[1], "team": p[2]} for p in PEOPLE],
        "repos": REPOS,
        "pull_requests": prs,
        "deployments": deployments,
        "incidents": incidents,
    }
