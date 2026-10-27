"""Azure DevOps 資料來源 — 把 REST API 的回應正規化成與 mock_source 相同的結構。

認證順序：環境變數 (config.azdo.pat_env) → macOS Keychain (pat_keychain_service)。
用到的 API（皆為唯讀）:
  GET /_apis/git/repositories
  GET /_apis/git/repositories/{repo}/pullrequests?searchCriteria.status=all
  GET /_apis/git/repositories/{repo}/pullRequests/{id}/commits
  GET /_apis/git/repositories/{repo}/pullRequests/{id}/threads
  GET /_apis/git/repositories/{repo}/pullRequests/{id}/iterations
  GET /_apis/pipelines/runs  或  /_apis/build/builds
  GET /_apis/distributedtask/... (environments / deployment records)
"""
import base64
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://dev.azure.com"
VSRM = "https://vsrm.dev.azure.com"


def _pat(cfg):
    tok = os.environ.get(cfg.get("pat_env", "AZDO_PAT"), "").strip()
    if tok:
        return tok
    svc = cfg.get("pat_keychain_service")
    if svc:
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", svc, "-w"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    raise SystemExit(
        f"找不到 Azure DevOps PAT。請設定環境變數 {cfg.get('pat_env','AZDO_PAT')}，"
        f"或執行: security add-generic-password -s {cfg.get('pat_keychain_service')} -a $USER -w '<PAT>'")


class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        self.org = cfg["organization"]
        self.project = cfg["project"]
        self.api_version = cfg.get("api_version", "7.1")
        auth = base64.b64encode(f":{_pat(cfg)}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

    def get(self, path, params=None, base=API):
        params = dict(params or {})
        params.setdefault("api-version", self.api_version)
        url = f"{base}/{self.org}/{urllib.parse.quote(self.project)}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    def paged(self, path, params=None, base=API, page=200, cap=5000):
        params = dict(params or {})
        skip, out = 0, []
        while len(out) < cap:
            params["$top"] = page
            params["$skip"] = skip
            batch = self.get(path, params, base).get("value", [])
            out.extend(batch)
            if len(batch) < page:
                break
            skip += page
        return out


def _iso(s):
    """Azure DevOps 回傳的時間字串 → 統一的 UTC ISO8601 Z 格式。"""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)          # 截掉超過 6 位的小數秒
    return datetime.fromisoformat(s).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ident(x):
    if not x:
        return (None, None)
    return (x.get("uniqueName") or x.get("displayName"), x.get("displayName"))


def fetch(cfg, now=None):
    az = cfg["azdo"]
    c = Client(az)
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=cfg.get("window_days", 28) + cfg.get("baseline_days", 28) + 7)

    repos = az.get("repos") or [r["name"] for r in c.get("/_apis/git/repositories")["value"]]
    people, prs = {}, []

    for repo in repos:
        rp = urllib.parse.quote(repo)
        raw = c.paged(f"/_apis/git/repositories/{rp}/pullrequests",
                      {"searchCriteria.status": "all", "searchCriteria.targetRefName": "refs/heads/main"})
        for pr in raw:
            created = _iso(pr.get("creationDate"))
            if created and created < since.isoformat().replace("+00:00", "Z"):
                continue
            uid, name = _ident(pr.get("createdBy"))
            if uid:
                people.setdefault(uid, {"id": uid, "name": name or uid, "team": ""})

            pid = pr["pullRequestId"]
            commits = [{
                "sha": cm.get("commitId"),
                "author": (cm.get("author") or {}).get("email"),
                "authored_at": _iso((cm.get("author") or {}).get("date")),
                "message": cm.get("comment") or "",
            } for cm in c.get(f"/_apis/git/repositories/{rp}/pullRequests/{pid}/commits").get("value", [])]

            threads = c.get(f"/_apis/git/repositories/{rp}/pullRequests/{pid}/threads").get("value", [])
            # 第一則「人類寫的」留言時間 = pickup time 的終點
            review_times = []
            comment_count = 0
            for t in threads:
                for cmt in t.get("comments", []):
                    if cmt.get("commentType") == "system":
                        continue
                    a_uid, _ = _ident(cmt.get("author"))
                    if a_uid and a_uid != uid:
                        review_times.append(_iso(cmt.get("publishedDate")))
                    comment_count += 1
            first_review = min([x for x in review_times if x], default=None)

            # 核准時間：取最早一位投 vote>=10 的 reviewer
            approvals = [_iso(pr.get("closedDate"))] if pr.get("status") == "completed" else []
            reviewers = []
            for rv in pr.get("reviewers", []):
                r_uid, r_name = _ident(rv)
                if r_uid:
                    reviewers.append(r_uid)
                    people.setdefault(r_uid, {"id": r_uid, "name": r_name or r_uid, "team": ""})
            first_approval = min([a for a in approvals if a], default=None)

            # 變更行數需逐 iteration 取 diff，成本高 → 以 commit changeCounts 近似
            stats = _pr_size(c, rp, pid)
            merged = _iso(pr.get("closedDate")) if pr.get("status") == "completed" else None

            prs.append({
                "id": pid,
                "repo": repo,
                "title": pr.get("title", ""),
                "author": uid,
                "author_name": name or uid,
                "team": "",
                "first_commit_at": min([x["authored_at"] for x in commits if x["authored_at"]], default=created),
                "created_at": created,
                "first_review_at": first_review,
                "first_approval_at": first_approval or first_review,
                "merged_at": merged,
                "closed_at": _iso(pr.get("closedDate")),
                "status": pr.get("status"),             # active / completed / abandoned
                "additions": stats["additions"],
                "deletions": stats["deletions"],
                "files_changed": stats["files"],
                "reviewers": reviewers,
                "comment_count": comment_count,
                "pushes_after_approval": 0,
                "work_item_type": "",
                "target_branch": (pr.get("targetRefName") or "").replace("refs/heads/", ""),
                "labels": [l.get("name") for l in pr.get("labels", [])],
                "commits": commits,
                "is_revert": bool(re.match(r"(?i)^revert", pr.get("title", ""))),
            })

    deployments, incidents = _fetch_deployments(c, cfg, repos)
    return {
        "source": "azdo",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "now": now.isoformat().replace("+00:00", "Z"),
        "people": list(people.values()),
        "repos": repos,
        "pull_requests": prs,
        "deployments": deployments,
        "incidents": incidents,
    }


def _pr_size(c, repo_q, pid):
    """用最後一個 iteration 的 changes 概算 PR 大小。"""
    try:
        its = c.get(f"/_apis/git/repositories/{repo_q}/pullRequests/{pid}/iterations").get("value", [])
        if not its:
            return {"additions": 0, "deletions": 0, "files": 0}
        last = its[-1]["id"]
        ch = c.get(f"/_apis/git/repositories/{repo_q}/pullRequests/{pid}/iterations/{last}/changes")
        entries = ch.get("changeEntries", [])
        return {"additions": 0, "deletions": 0, "files": len(entries)}
    except Exception:
        return {"additions": 0, "deletions": 0, "files": 0}


def _fetch_deployments(c, cfg, repos):
    """部署與失敗事件。優先讀 Pipelines runs；沒有就退回 Builds。"""
    az = cfg["azdo"]
    want = set(az.get("deployment_pipelines") or [])
    deployments, incidents = [], []
    try:
        runs = c.get("/_apis/pipelines/runs").get("value", [])
    except Exception:
        runs = c.get("/_apis/build/builds", {"statusFilter": "completed"}).get("value", [])
    for r in runs:
        pname = (r.get("pipeline") or {}).get("name") or (r.get("definition") or {}).get("name") or ""
        if want and pname not in want:
            continue
        res = (r.get("result") or "").lower()
        fin = _iso(r.get("finishedDate") or r.get("finishTime"))
        if not fin:
            continue
        deployments.append({
            "id": r.get("id"), "repo": pname, "pipeline": pname,
            "environment": "Production", "finished_at": fin,
            "result": "failed" if res in ("failed", "canceled") else "succeeded",
            "pr_id": None, "commit_sha": None,
        })
    # 失敗事件：以「失敗部署 → 下一次同管線成功部署」當作復原時間
    by_pipe = {}
    for d in sorted(deployments, key=lambda x: x["finished_at"]):
        by_pipe.setdefault(d["pipeline"], []).append(d)
    for pipe, ds in by_pipe.items():
        for i, d in enumerate(ds):
            if d["result"] == "failed":
                nxt = next((x for x in ds[i + 1:] if x["result"] == "succeeded"), None)
                incidents.append({
                    "id": f"INC-{d['id']}", "repo": pipe, "deployment_id": d["id"],
                    "started_at": d["finished_at"],
                    "resolved_at": nxt["finished_at"] if nxt else None,
                })
    return deployments, incidents
