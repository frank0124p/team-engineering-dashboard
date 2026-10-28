#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""互動式設定 —— 貼上 PAT、確認組織/專案、填團隊名單，就能開始用。

    /usr/bin/python3 bin/setup.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import config_loader                                                      # noqa: E402
from azdo_client import (Client, AzdoError,                                # noqa: E402
                         whoami, list_orgs, list_projects, list_repos)


def _store_pat(cfg, pat):
    """優先存 Keychain；非 macOS 或失敗就寫 .env（兩者都在 .gitignore 裡）。"""
    env_key = cfg.get("azdo", {}).get("pat_env", "AZDO_PAT")
    svc = cfg.get("azdo", {}).get("pat_keychain_service", "azdo-pat")
    if sys.platform == "darwin":
        subprocess.run(["security", "delete-generic-password", "-s", svc],
                       capture_output=True)
        r = subprocess.run(["security", "add-generic-password", "-s", svc,
                            "-a", os.environ.get("USER", "me"), "-w", pat],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"   ✅ 已存入 macOS Keychain（{svc}）")
            return
    with open(config_loader.ENV_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{env_key}={pat}\n")
    os.chmod(config_loader.ENV_FILE, 0o600)
    print(f"   ✅ 已寫入 .env（權限 600，已被 .gitignore 排除）")


def ask(prompt, default=""):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def pick(label, options):
    if not options:
        raise AzdoError(f"沒有可選的{label}。")
    if len(options) == 1:
        print(f"  {label}：{options[0]}（只有這一個，直接用）")
        return options[0]
    print(f"\n  找到 {len(options)} 個{label}：")
    for i, o in enumerate(options, 1):
        print(f"    {i}. {o}")
    while True:
        s = input(f"  選一個（1-{len(options)}）：").strip()
        if s.isdigit() and 1 <= int(s) <= len(options):
            return options[int(s) - 1]


def main():
    cfg = config_loader.load()
    print("━" * 58)
    print(" 團隊工程產出儀表板 — 設定")
    print("━" * 58)

    # 1. PAT
    pat, src = config_loader.resolve_pat(cfg)
    if pat and ask(f"\n① PAT：已從{src}取得。要換一個嗎？(y/N)", "n").lower() != "y":
        pass
    else:
        print("\n① PAT")
        print("   Azure DevOps → 右上頭像 → Personal access tokens → New Token")
        print("   權限勾：Code(Read)、Build(Read)、Work Items(Read)")
        import getpass
        pat = getpass.getpass("   貼上 PAT（不會顯示）：").strip()
        if not pat:
            raise AzdoError("沒有輸入 PAT")
        _store_pat(cfg, pat)

    c = Client(pat)
    me = whoami(c)
    print(f"   ✅ 驗證成功，登入身分：{me['name']}（{me['email']}）")

    # 2. 組織 / 專案 / repo
    print("\n② 範圍")
    scope = cfg.setdefault("azdo", {})
    scope["organization"] = scope.get("organization") or pick("組織", list_orgs(c, me["id"]))
    scope["project"] = scope.get("project") or pick(
        "專案", list_projects(c, scope["organization"]))
    repos = list_repos(c, scope["organization"], scope["project"])
    print(f"  repo：找到 {len(repos)} 個 —— {', '.join(repos[:6])}{' …' if len(repos) > 6 else ''}")
    if ask("  全部都要追蹤嗎？(Y/n)", "y").lower() == "n":
        sel = ask("  只追蹤哪些（逗號分隔）")
        scope["repos"] = [r.strip() for r in sel.split(",") if r.strip()]
    else:
        scope["repos"] = []          # 留空＝每次執行都重新抓全部

    # 3. 團隊名單
    print("\n③ 團隊成員")
    print("   貼上 email 或顯示名稱，一行一個；直接按 Enter 結束。")
    print("   （全部留空＝抓專案裡所有人，只排除機器人）")
    members = list(cfg.get("team", {}).get("members") or [])
    if members:
        print(f"   目前有 {len(members)} 位：{', '.join(members[:5])}{' …' if len(members) > 5 else ''}")
        if ask("   重新輸入？(y/N)", "n").lower() == "y":
            members = []
        else:
            members = members
    if not members:
        while True:
            line = input("   > ").strip()
            if not line:
                break
            members.extend([x.strip() for x in line.split(",") if x.strip()])
    cfg.setdefault("team", {})["members"] = members
    cfg["team"]["name"] = ask("   團隊名稱", cfg["team"].get("name") or "我的團隊")

    path = config_loader.save_local({
        "source": "azdo",
        "azdo": {"organization": scope["organization"], "project": scope["project"],
                 "repos": scope.get("repos") or []},
        "team": {"name": cfg["team"]["name"], "members": members},
    })
    print(f"\n✅ 已寫入 {path}（這支不會進 git）")
    print(f"   組織 {scope['organization']} / 專案 {scope['project']}"
          f" / 成員 {len(members) or '全部'} 位")
    print("\n接下來：")
    print("   /usr/bin/python3 bin/dashboard.py            # 產生儀表板")
    print("   /usr/bin/python3 bin/dashboard.py --summary  # 文字摘要")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AzdoError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
