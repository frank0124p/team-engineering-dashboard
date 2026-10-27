#!/bin/bash
# 每日更新儀表板並輸出文字摘要（stdout 可直接丟 Discord）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
/usr/bin/python3 bin/dashboard.py --artifact --quiet
/usr/bin/python3 bin/dashboard.py --summary --quiet
