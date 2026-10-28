#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""設定載入與身分比對 — 讓這份儀表板搬到任何一台機器都只要兩件事：PAT 與成員名單。

設定來源（後面的覆蓋前面的）：
  1. config.json            進 git 的預設值，不放機密、不放公司資訊
  2. config.local.json      這台機器的設定（.gitignore 已排除）← setup.py 寫這支
  3. 環境變數               TD_* 系列，給 CI／容器用
  4. 命令列參數             臨時覆蓋

PAT 來源（依序嘗試，不寫進任何會進 git 的檔案）：
  1. 環境變數（預設 AZDO_PAT）
  2. 專案根目錄的 .env（KEY=VALUE，同樣被 .gitignore 排除）
  3. macOS Keychain（只有 macOS 有；其他平台自動跳過）

身分比對：Azure DevOps 的 PR 作者會帶 uniqueName（通常是公司 email）與 displayName。
成員名單因此可以混著寫 email、帳號、顯示名，全部正規化後比對：
  ["Amy Chen", "ben.liu@corp.com", "carol.wu"]
"""
import json
import os
import re
import subprocess
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "config.json")
LOCAL_CONFIG = os.path.join(ROOT, "config.local.json")
ENV_FILE = os.path.join(ROOT, ".env")

# 環境變數 → config 路徑
ENV_MAP = {
    "TD_SOURCE": ("source",),
    "TD_ORG": ("azdo", "organization"),
    "TD_PROJECT": ("azdo", "project"),
    "TD_REPOS": ("azdo", "repos"),              # 逗號分隔，空 = 全部 repo
    "TD_WINDOW": ("window_days",),
    "TD_TEAM_NAME": ("team", "name"),
    "TD_MEMBERS": ("team", "members"),          # 逗號／分號／換行分隔
    "TD_MEMBERS_MODE": ("team", "members_mode"),
}
LIST_KEYS = {("azdo", "repos"), ("team", "members")}
INT_KEYS = {("window_days",), ("baseline_days",)}


# ── 讀檔與合併 ───────────────────────────────────────────────────
def _deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _set_path(cfg, path, value):
    d = cfg
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def split_list(raw):
    """「a, b; c\nd」→ ["a","b","c","d"]"""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in re.split(r"[,;\n]+", str(raw or "")) if s.strip()]


def read_env_file(path=ENV_FILE):
    """讀 .env（KEY=VALUE）。不覆蓋已存在的環境變數。"""
    out = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load(config_path=None, cli=None):
    """回傳合併後的 cfg。cli 是 dict，只有非 None 的值會覆蓋。"""
    path = config_path or DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    if os.path.exists(LOCAL_CONFIG) and os.path.abspath(path) != os.path.abspath(LOCAL_CONFIG):
        try:
            with open(LOCAL_CONFIG, encoding="utf-8") as f:
                _deep_merge(cfg, json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️ config.local.json 讀不起來，先忽略：{e}", file=sys.stderr)

    env = dict(read_env_file())
    env.update(os.environ)          # 真的環境變數優先
    for ev, path_t in ENV_MAP.items():
        raw = env.get(ev)
        if raw is None or str(raw).strip() == "":
            continue
        if path_t in LIST_KEYS:
            _set_path(cfg, path_t, split_list(raw))
        elif path_t in INT_KEYS:
            try:
                _set_path(cfg, path_t, int(raw))
            except ValueError:
                pass
        else:
            _set_path(cfg, path_t, str(raw).strip())

    for k, v in (cli or {}).items():
        if v is None:
            continue
        _set_path(cfg, (k,) if isinstance(k, str) else k, v)

    cfg.setdefault("team", {}).setdefault("members", [])
    cfg["team"]["members"] = split_list(cfg["team"].get("members"))
    cfg["team"].setdefault("members_mode", "include")
    cfg.setdefault("_paths", {})["root"] = ROOT
    return cfg


def save_local(patch):
    """把設定寫進 config.local.json（合併既有內容）。"""
    cur = {}
    if os.path.exists(LOCAL_CONFIG):
        try:
            with open(LOCAL_CONFIG, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, json.JSONDecodeError):
            cur = {}
    _deep_merge(cur, patch)
    with open(LOCAL_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    return LOCAL_CONFIG


# ── PAT ─────────────────────────────────────────────────────────
def resolve_pat(cfg, required=False):
    az = cfg.get("azdo", {})
    env_key = az.get("pat_env", "AZDO_PAT")
    tok = (os.environ.get(env_key) or "").strip()
    if tok:
        return tok, f"環境變數 {env_key}"
    tok = (read_env_file().get(env_key) or "").strip()
    if tok:
        return tok, ".env"
    svc = az.get("pat_keychain_service")
    if svc and sys.platform == "darwin":
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", svc, "-w"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), f"macOS Keychain（{svc}）"
        except Exception:
            pass
    if required:
        raise SystemExit(pat_help(cfg))
    return None, None


def pat_help(cfg):
    az = cfg.get("azdo", {})
    env_key = az.get("pat_env", "AZDO_PAT")
    svc = az.get("pat_keychain_service", "azdo-pat")
    lines = [
        "找不到 Azure DevOps PAT。三種放法挑一種：",
        f"  1. 環境變數：export {env_key}='<PAT>'",
        f"  2. 專案根目錄 .env 檔：{env_key}=<PAT>   （已被 .gitignore 排除）",
    ]
    if sys.platform == "darwin":
        lines.append(f"  3. macOS Keychain：security add-generic-password -s {svc} -a \"$USER\" -w '<PAT>'")
    lines.append("或直接跑一次引導設定：python3 bin/setup.py")
    return "\n".join(lines)


# ── 成員名單比對 ─────────────────────────────────────────────────
_PUNCT = re.compile(r"[\s\.\-_,'’]+")


def norm_person(s):
    """人名／帳號／email 正規化成比對用的 key。"""
    s = unicodedata.normalize("NFKC", str(s or "")).strip().lower()
    if not s:
        return ""
    return _PUNCT.sub("", s)


def person_keys(author=None, author_name=None, email=None):
    """一筆 PR 的作者可以用哪些 key 比對（含 email 的 local part）。"""
    keys = set()
    for raw in (author, author_name, email):
        if not raw:
            continue
        raw = str(raw).strip()
        keys.add(norm_person(raw))
        if "@" in raw:
            keys.add(norm_person(raw.split("@", 1)[0]))
    return {k for k in keys if k}


class Roster:
    """成員名單。名單空的時候自動放行所有人（不要因為沒填就整份空白）。"""

    def __init__(self, cfg):
        team = cfg.get("team", {})
        self.raw = split_list(team.get("members"))
        self.mode = team.get("members_mode", "include")
        self.index = {}
        for m in self.raw:
            for k in person_keys(m, m, m if "@" in m else None):
                self.index[k] = m
        self.enabled = bool(self.index) and self.mode == "include"
        self.hits = {}           # 名單條目 → 命中幾筆
        self.unknown = {}        # 名單外看到的人 → 幾筆

    def match(self, author=None, author_name=None, email=None):
        """回傳名單中對應的那一條（沒對到回 None）。"""
        for k in person_keys(author, author_name, email):
            m = self.index.get(k)
            if m:
                return m
        return None

    def includes(self, author=None, author_name=None, email=None):
        m = self.match(author, author_name, email)
        label = author_name or author or "?"
        if m:
            self.hits[m] = self.hits.get(m, 0) + 1
        else:
            self.unknown[label] = self.unknown.get(label, 0) + 1
        if not self.enabled:
            return True
        return m is not None

    def report(self):
        missing = [m for m in self.raw if not self.hits.get(m)]
        return {
            "mode": "只算名單內" if self.enabled else ("名單空白→全員計入" if not self.index else "全員計入"),
            "members": self.raw,
            "matched": sorted(self.hits.items(), key=lambda x: -x[1]),
            "missing": missing,
            "not_in_roster": sorted(self.unknown.items(), key=lambda x: -x[1])[:20],
        }


def ready(cfg):
    """搬到新機器後「現在能不能跑」。

    回傳 (ok, blockers, warnings)。
    org/project 留空不算問題 —— azdo_client.discover() 會用 PAT 反推，
    只有在你有多個組織/專案時才需要指定。
    """
    blockers, warnings = [], []
    tok, _src = resolve_pat(cfg)
    if not tok:
        blockers.append("PAT（沒有它只能跑示範資料）")
    if not split_list(cfg.get("team", {}).get("members")):
        warnings.append("team.members 是空的 → 會計入專案內所有人（排除機器人）。"
                        "要只看自己團隊就把名字列進去")
    src = cfg.get("source")
    if src not in (None, "", "auto", "azdo", "demo"):
        warnings.append(f'source="{src}" 不是 auto/azdo/demo，會被當成 auto')
    return (not blockers), blockers, warnings
