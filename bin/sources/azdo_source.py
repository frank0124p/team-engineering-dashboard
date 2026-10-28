# -*- coding: utf-8 -*-
"""Azure DevOps 來源：把 REST 回應正規化成與 mock_source 相同的結構。

只需要 PAT 與團隊名單；組織／專案／repo 沒填就自動偵測。
PR 大小預設以「改了幾個檔」計算 —— Azure 的 API 不直接提供行數，
硬湊出來的行數會失真，寧可用一個準確的單位。
"""
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from azdo_client import Client, AzdoError, discover            # noqa: E402
from config_loader import Roster, resolve_pat                 # noqa: E402


def _iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)          # 小數秒超過 6 位 fromisoformat 會炸
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _ident(x):
    if not x:
        return (None, None)
    return (x.get("uniqueName") or x.get("mailAddress") or x.get("displayName"),
            x.get("displayName"))


def fetch(cfg, now=None):
    pat, pat_src = resolve_pat(cfg, required=True)
    c = Client(pat, cfg.get("azdo", {}).get("api_version", "7.1"))
    scope, me = discover(c, cfg)
    org, project = scope["organization"], scope["project"]
    roster = Roster(cfg)

    now = now or datetime.now(timezone.utc)
    lookback = cfg.get("window_days", 28) + cfg.get("baseline_days", 28) + 7
    since = (now - timedelta(days=lookback)).isoformat().replace("+00:00", "Z")

    size_metric = cfg.get("pr_size_metric", "files")
    people, prs = {}, []
    pr_merge_sha = {}

    for repo in scope["repos"]:
        rp = urllib.parse.quote(repo)
        try:
            raw = c.paged(org, project, f"/_apis/git/repositories/{rp}/pullrequests",
                          {"searchCriteria.status": "all"})
        except AzdoError as e:
            print(f"[warn] repo {repo} 讀取失敗，略過：{e}", file=sys.stderr)
            continue

        for pr in raw:
            created = _iso(pr.get("creationDate"))
            if not created or created < since:
                continue
            uid, dname = _ident(pr.get("createdBy"))
            if not roster.includes(author=uid, author_name=dname, email=uid):
                continue
            people.setdefault(uid, {"id": uid, "name": dname or uid, "team": ""})

            pid = pr["pullRequestId"]
            base = f"/_apis/git/repositories/{rp}/pullRequests/{pid}"
            commits = [{
                "sha": cm.get("commitId"),
                "author": ((cm.get("author") or {}).get("email")),
                "authored_at": _iso((cm.get("author") or {}).get("date")),
                "message": cm.get("comment") or "",
            } for cm in c.get(org, project, base + "/commits").get("value", [])]

            first_review, first_approval, comment_count, pushes_after = \
                _review_timeline(c, org, project, base, uid, pr)

            size = _pr_size(c, org, project, rp, pid, size_metric)
            merged = _iso(pr.get("closedDate")) if pr.get("status") == "completed" else None
            merge_sha = (pr.get("lastMergeCommit") or {}).get("commitId")
            if merged and merge_sha:
                pr_merge_sha[merge_sha] = pid

            prs.append({
                "id": pid,
                "repo": repo,
                "title": pr.get("title", ""),
                "author": uid,
                "author_name": dname or uid,
                "team": "",
                "first_commit_at": min([x["authored_at"] for x in commits
                                        if x["authored_at"]], default=created),
                "created_at": created,
                "first_review_at": first_review,
                "first_approval_at": first_approval or first_review,
                "merged_at": merged,
                "closed_at": _iso(pr.get("closedDate")),
                "status": pr.get("status"),
                "additions": size["additions"],
                "deletions": size["deletions"],
                "files_changed": size["files"],
                "reviewers": [_ident(rv)[0] for rv in pr.get("reviewers", []) if _ident(rv)[0]],
                "comment_count": comment_count,
                "pushes_after_approval": pushes_after,
                "work_item_type": "",
                "target_branch": (pr.get("targetRefName") or "").replace("refs/heads/", ""),
                "labels": [l.get("name") for l in (pr.get("labels") or [])],
                "commits": commits,
                "is_revert": bool(re.match(r"(?i)^\s*revert", pr.get("title", ""))),
            })

    _attach_work_item_types(c, org, project, prs)
    deployments, incidents = _deployments(c, org, project, cfg, scope, pr_merge_sha, since)

    return {
        "source": "azdo",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "now": now.isoformat().replace("+00:00", "Z"),
        "scope": {"organization": org, "project": project,
                  "repos": scope["repos"], "pat_source": pat_src, "signed_in_as": me.get("name")},
        "roster_report": roster.report(),
        "size_metric": size_metric,
        "people": list(people.values()),
        "repos": scope["repos"],
        "pull_requests": prs,
        "deployments": deployments,
        "incidents": incidents,
    }


def _review_timeline(c, org, project, base, author_uid, pr):
    """從討論串推出首次審查、核准時間、留言數、核准後又推了幾次。"""
    threads = c.get(org, project, base + "/threads").get("value", [])
    review_times, approve_times, comments = [], [], 0
    for t in threads:
        props = t.get("properties") or {}
        vote = (props.get("CodeReviewVoteResult") or {}).get("$value")
        t_time = _iso(t.get("publishedDate"))
        if vote is not None and int(vote) >= 10 and t_time:
            approve_times.append(t_time)
        for cm in t.get("comments", []):
            if cm.get("commentType") == "system":
                continue
            a_uid, _ = _ident(cm.get("author"))
            comments += 1
            if a_uid and a_uid != author_uid:
                ts = _iso(cm.get("publishedDate"))
                if ts:
                    review_times.append(ts)
    first_review = min(review_times, default=None)
    first_approval = min(approve_times, default=None)

    pushes_after = 0
    if first_approval:
        its = c.get(org, project, base + "/iterations").get("value", [])
        pushes_after = len([i for i in its
                            if (_iso(i.get("createdDate")) or "") > first_approval])
    return first_review, first_approval, comments, pushes_after


def _pr_size(c, org, project, repo_q, pid, metric):
    """PR 大小。

    Azure DevOps 的 PR API 不提供增刪行數，硬估會失真 —— 所以預設改用
    「變更檔案數」這個 API 直接給、而且準確的單位。
    需要行數時把 pr_size_metric 設成 lines，另由本機 git 取得（見 README）。
    """
    try:
        its = c.get(org, project,
                    f"/_apis/git/repositories/{repo_q}/pullRequests/{pid}/iterations"
                    ).get("value", [])
        if not its:
            return {"additions": 0, "deletions": 0, "files": 0}
        last = its[-1]["id"]
        ch = c.get(org, project,
                   f"/_apis/git/repositories/{repo_q}/pullRequests/{pid}"
                   f"/iterations/{last}/changes")
        entries = [e for e in ch.get("changeEntries", [])
                   if not (e.get("item") or {}).get("isFolder")]
        return {"additions": 0, "deletions": 0, "files": len(entries)}
    except AzdoError:
        return {"additions": 0, "deletions": 0, "files": 0}


def _attach_work_item_types(c, org, project, prs):
    """PR 關聯的工作項目類型 → Impact 面向的工作配比。"""
    for pr in prs:
        try:
            links = c.get(org, project,
                          f"/_apis/git/repositories/{urllib.parse.quote(pr['repo'])}"
                          f"/pullRequests/{pr['id']}/workitems").get("value", [])
            if not links:
                continue
            wid = links[0].get("id")
            wi = c.get(org, project, f"/_apis/wit/workitems/{wid}",
                       {"fields": "System.WorkItemType"})
            pr["work_item_type"] = (wi.get("fields") or {}).get("System.WorkItemType", "")
        except AzdoError:
            continue


def _deployments(c, org, project, cfg, scope, pr_merge_sha, since):
    """生產環境部署與失敗事件。用 commit SHA 把部署接回 PR，
    AI PR vs 人工 PR 的變更失敗率才算得出來。"""
    want = set(scope.get("deployment_pipelines") or [])
    prod = {p.lower() for p in (scope.get("production_environments") or ["production"])}
    deployments, incidents = [], []

    try:
        runs = c.get(org, project, "/_apis/pipelines/runs").get("value", [])
        get_sha = lambda r: (((r.get("resources") or {}).get("repositories") or {})
                             .get("self") or {}).get("version")
        get_name = lambda r: (r.get("pipeline") or {}).get("name") or ""
        get_fin = lambda r: r.get("finishedDate")
    except AzdoError:
        runs = c.paged(org, project, "/_apis/build/builds", {"statusFilter": "completed"})
        get_sha = lambda r: r.get("sourceVersion")
        get_name = lambda r: (r.get("definition") or {}).get("name") or ""
        get_fin = lambda r: r.get("finishTime")

    for r in runs:
        name = get_name(r)
        if want and name not in want:
            continue
        if not want and prod and not any(p in name.lower() for p in prod):
            # 沒指定管線時，用名稱篩掉明顯不是部署的（CI、PR 驗證）
            if re.search(r"(?i)\b(ci|pr|build|test|lint)\b", name):
                continue
        fin = _iso(get_fin(r))
        if not fin or fin < since:
            continue
        sha = get_sha(r)
        res = (r.get("result") or "").lower()
        deployments.append({
            "id": r.get("id"), "repo": name, "pipeline": name,
            "environment": "Production", "finished_at": fin,
            "result": "failed" if res in ("failed", "canceled") else "succeeded",
            "pr_id": pr_merge_sha.get(sha), "commit_sha": sha,
        })

    by_pipe = {}
    for d in sorted(deployments, key=lambda x: x["finished_at"]):
        by_pipe.setdefault(d["pipeline"], []).append(d)
    for ds in by_pipe.values():
        for i, d in enumerate(ds):
            if d["result"] != "failed":
                continue
            nxt = next((x for x in ds[i + 1:] if x["result"] == "succeeded"), None)
            incidents.append({
                "id": f"INC-{d['id']}", "repo": d["pipeline"], "deployment_id": d["id"],
                "started_at": d["finished_at"],
                "resolved_at": nxt["finished_at"] if nxt else None,
            })
    return deployments, incidents
