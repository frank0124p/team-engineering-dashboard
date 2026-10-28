# -*- coding: utf-8 -*-
"""Azure DevOps 連線層：PAT 取得、自動偵測組織/專案、成員比對。

設計目標：使用者只要提供 PAT 與成員名單，其餘自己找出來。
"""
import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

API = "https://dev.azure.com"
PROFILE = "https://app.vssps.visualstudio.com"
UA = "team-dashboard/1.0"


class AzdoError(RuntimeError):
    pass


# ── HTTP ──────────────────────────────────────────────────────────────────

class Client:
    def __init__(self, pat, api_version="7.1"):
        self.auth = base64.b64encode(f":{pat}".encode()).decode()
        self.api_version = api_version

    def _req(self, url, params=None):
        params = dict(params or {})
        params.setdefault("api-version", self.api_version)
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full, headers={
            "Authorization": f"Basic {self.auth}",
            "Accept": "application/json",
            "User-Agent": UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code in (401, 203):
                raise AzdoError("PAT 無效或已過期（401）。請確認權限至少包含 Code(Read)、Build(Read)。")
            if e.code == 403:
                raise AzdoError(f"PAT 權限不足（403）：{detail}")
            if e.code == 404:
                raise AzdoError(f"找不到資源（404）：{full.split('?')[0]}")
            raise AzdoError(f"HTTP {e.code}：{detail}")
        # 未授權時 Azure 會回登入 HTML 而不是 JSON
        if body.lstrip().startswith("<"):
            raise AzdoError("收到 HTML 而非 JSON —— 幾乎都是 PAT 無效或組織名稱打錯。")
        return json.loads(body) if body else {}

    def get(self, org, project, path, params=None, base=API):
        proj = f"/{urllib.parse.quote(project)}" if project else ""
        return self._req(f"{base}/{org}{proj}{path}", params)

    def paged(self, org, project, path, params=None, page=200, cap=4000):
        params, skip, out = dict(params or {}), 0, []
        while len(out) < cap:
            params["$top"], params["$skip"] = page, skip
            batch = self.get(org, project, path, params).get("value", [])
            out.extend(batch)
            if len(batch) < page:
                break
            skip += page
        return out


# ── 自動偵測 ───────────────────────────────────────────────────────────────

def whoami(client):
    """PAT 對應的人。順便當作 PAT 驗證。"""
    p = client._req(f"{PROFILE}/_apis/profile/profiles/me")
    return {"id": p.get("id"), "name": p.get("displayName"),
            "email": p.get("emailAddress")}


def list_orgs(client, member_id):
    data = client._req(f"{PROFILE}/_apis/accounts", {"memberId": member_id})
    return [a["accountName"] for a in data.get("value", [])]


def list_projects(client, org):
    return [p["name"] for p in client.get(org, None, "/_apis/projects",
                                          {"stateFilter": "wellFormed"}).get("value", [])]


def list_repos(client, org, project):
    return [r["name"] for r in
            client.get(org, project, "/_apis/git/repositories").get("value", [])]


def discover(client, cfg):
    """把 azdo 區段沒填的欄位補齊。只有一個候選就直接用，多個就請使用者指定。"""
    scope = dict(cfg.get("azdo") or {})
    me = whoami(client)

    if not scope.get("organization"):
        orgs = list_orgs(client, me["id"])
        if not orgs:
            raise AzdoError("這個 PAT 看不到任何組織。確認 PAT 是在正確的組織底下建立的。")
        if len(orgs) > 1:
            raise AzdoError("偵測到多個組織，請在 config.json 的 scope.organization 指定其一：\n  "
                            + "\n  ".join(orgs))
        scope["organization"] = orgs[0]

    if not scope.get("project"):
        projects = list_projects(client, scope["organization"])
        if not projects:
            raise AzdoError(f"組織 {scope['organization']} 底下沒有看得到的專案。")
        if len(projects) > 1:
            raise AzdoError("偵測到多個專案，請在 config.json 的 scope.project 指定其一：\n  "
                            + "\n  ".join(projects))
        scope["project"] = projects[0]

    if not scope.get("repos"):
        scope["repos"] = list_repos(client, scope["organization"], scope["project"])

    return scope, me


