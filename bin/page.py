# -*- coding: utf-8 -*-
"""把指標組裝成完整頁面。standalone=True 產生本機可開的 HTML；
False 則產生 Artifact 用的 body 片段（不含 html/head/body 標籤）。"""
import metrics
import ai_section
from render import (esc, fmt_h, fmt_num, delta_html, legend, table, details_table,
                    chart_weekly_stack, chart_breakdown, chart_hist, chart_line, chart_hbars)

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">')

CSS = """
:root{
  --plane:#f6f6f3; --surface:#fcfcfb; --sunk:#f1f1ec;
  --ink:#16161a; --ink2:#57564f; --muted:#8b8a83;
  --rule:#e4e3db; --grid:#e1e0d9; --axis:#c3c2b7;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --up:#006300; --down:#d03b3b;
  --ui:"IBM Plex Sans",system-ui,-apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --plane:#0d0d0d; --surface:#1a1a19; --sunk:#232321;
  --ink:#f6f6f1; --ink2:#bdbcb3; --muted:#8b8a83;
  --rule:#2b2b28; --grid:#2c2c2a; --axis:#383835;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --up:#0ca30c;
}}
:root[data-theme="dark"]{
  --plane:#0d0d0d; --surface:#1a1a19; --sunk:#232321;
  --ink:#f6f6f1; --ink2:#bdbcb3; --muted:#8b8a83;
  --rule:#2b2b28; --grid:#2c2c2a; --axis:#383835;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --up:#0ca30c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-family:var(--ui);
  font-size:15px;line-height:1.62;-webkit-font-smoothing:antialiased}
.wrap{max-width:1100px;margin:0 auto;padding:40px 22px 80px}

/* ---- 頁首 ---- */
header.top{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;justify-content:space-between;
  padding-bottom:18px;border-bottom:2px solid var(--ink)}
h1{font-size:25px;font-weight:600;margin:0;letter-spacing:-.015em;text-wrap:balance}
.sub{color:var(--ink2);font-size:12.5px;margin:5px 0 0;font-variant-numeric:tabular-nums}
.badge{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;
  text-transform:uppercase;letter-spacing:.06em;padding:5px 10px;border:1px solid var(--rule);color:var(--ink2)}
.badge.demo{border-color:var(--warning);color:var(--ink);background:color-mix(in srgb,var(--warning) 14%,transparent)}

/* ---- 開頭結論 ---- */
.lede{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:0;
  border:1px solid var(--rule);border-top:none;background:var(--surface)}
.lede > div{padding:20px 22px}
.lede > div + div{border-left:1px solid var(--rule);background:var(--sunk)}
.lede p{margin:0 0 9px;font-size:14.5px;color:var(--ink2)}
.lede p:last-child{margin-bottom:0}
.lede b{color:var(--ink);font-weight:600}
.alerts{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px}
.alerts li{font-size:13.5px;color:var(--ink2);display:flex;gap:9px;align-items:flex-start;line-height:1.5}
.alerts li::before{content:"";flex:0 0 auto;width:3px;align-self:stretch;background:var(--warning)}
.ok{font-size:13.5px;color:var(--ink2);display:flex;gap:9px;align-items:center}

/* ---- 區塊 ---- */
section{background:var(--surface);border:1px solid var(--rule);margin-top:26px}
.shead{padding:18px 22px 14px;border-bottom:1px solid var(--rule)}
.eyebrow{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--muted);display:block;margin-bottom:5px}
h2{font-size:17px;font-weight:600;margin:0;letter-spacing:-.01em}
h3{font-size:14px;font-weight:600;margin:0 0 3px;letter-spacing:-.005em}
.note{color:var(--muted);font-size:12.5px;margin:5px 0 0;max-width:72ch;line-height:1.55}
.pad{padding:20px 22px}

/* ---- 指標格（細線切出的儀錶盤，不是浮卡） ---- */
.tiles{display:grid;gap:1px;background:var(--rule);border-bottom:1px solid var(--rule)}
.tiles.c4{grid-template-columns:repeat(4,minmax(0,1fr))}
.tiles.c3{grid-template-columns:repeat(3,minmax(0,1fr))}
.tile{background:var(--surface);padding:16px 20px 17px;border-left:3px solid transparent}
.tile.warn{border-left-color:var(--warning)}
.tile.crit{border-left-color:var(--critical)}
.tile .k{font-size:12.5px;color:var(--ink2);display:flex;justify-content:space-between;gap:8px;align-items:center}
.tile .v{font-family:var(--mono);font-size:28px;font-weight:500;line-height:1.2;margin:9px 0 4px;
  letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.tile .v .u{font-family:var(--ui);font-size:14px;font-weight:400;color:var(--ink2);letter-spacing:0}
.tile .f{font-size:11.5px;color:var(--muted);line-height:1.5}
.pill{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.07em;
  padding:3px 7px;border:1px solid var(--rule);white-space:nowrap}
.pill.good{color:var(--good);border-color:var(--good)}
.pill.warning{color:var(--ink);border-color:var(--warning);background:color-mix(in srgb,var(--warning) 18%,transparent)}
.pill.critical{color:var(--critical);border-color:var(--critical)}
.pill.muted{color:var(--muted)}
.delta{font-family:var(--mono);font-size:11.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.delta.up{color:var(--up)} .delta.down{color:var(--down)} .delta.muted{color:var(--muted)}

/* ---- 圖表 ---- */
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:0 0 12px;font-size:12.5px;color:var(--ink2)}
.lg{display:inline-flex;align-items:center;gap:6px}
.lg i{width:10px;height:10px;border-radius:2px;display:inline-block}
svg.chart{display:block;width:100%;height:auto;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}
.baseline{stroke:var(--axis);stroke-width:1}
.target{stroke:var(--critical);stroke-width:2}
text.axis{fill:var(--muted);font-family:var(--mono);font-size:10.5px;font-variant-numeric:tabular-nums}
text.vlabel{fill:var(--ink2);font-family:var(--mono);font-size:11.5px;font-variant-numeric:tabular-nums}
text.rlabel{fill:var(--ink2);font-size:12.5px}
text.seg{fill:#fff;font-family:var(--mono);font-size:12px;font-weight:500;font-variant-numeric:tabular-nums}
polyline.line{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
circle.dot{fill:var(--s1);stroke:var(--surface);stroke-width:2}
.mk{cursor:default;transition:opacity .12s}
.mk:hover{opacity:.8}

/* ---- 表格 ---- */
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:540px}
caption{text-align:left;color:var(--muted);font-size:12px;padding-bottom:7px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--rule);white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:11px;letter-spacing:.04em;border-bottom-color:var(--axis)}
td{font-variant-numeric:tabular-nums;font-size:13.5px}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--sunk)}
details.dt{margin-top:14px;border-top:1px solid var(--rule);padding-top:10px}
details.dt summary{cursor:pointer;color:var(--muted);font-family:var(--mono);font-size:11px;
  text-transform:uppercase;letter-spacing:.07em;padding:3px 0}
details.dt summary:focus-visible{outline:2px solid var(--s1);outline-offset:3px}

.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:0;
  border-top:1px solid var(--rule)}
.grid2 > div{padding:20px 22px}
.grid2 > div + div{border-left:1px solid var(--rule)}
.empty{color:var(--muted);font-size:13px}
.flag{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:1px}
.defs{columns:2;column-gap:32px;font-size:12.5px;color:var(--ink2)}
.defs p{break-inside:avoid;margin:0 0 10px;line-height:1.55}
.defs b{color:var(--ink);font-weight:600}
.defs code{font-family:var(--mono);font-size:11.5px;background:var(--sunk);padding:1px 4px}
.warnbox{border-left:3px solid var(--warning);background:var(--sunk);padding:14px 18px;
  font-size:13px;color:var(--ink2);line-height:1.6}
.warnbox b{color:var(--ink)}
#tip{position:fixed;pointer-events:none;opacity:0;transform:translate(-50%,-135%);z-index:50;
  background:var(--ink);color:var(--surface);font-size:12px;font-family:var(--mono);
  padding:6px 9px;white-space:nowrap;transition:opacity .1s}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media (max-width:1000px){ .tiles.c4,.tiles.c3{grid-template-columns:repeat(2,minmax(0,1fr))} }
@media (max-width:560px){ .tiles.c4,.tiles.c3{grid-template-columns:1fr} }
@media (max-width:760px){
  .lede{grid-template-columns:1fr}
  .lede > div + div{border-left:none;border-top:1px solid var(--rule)}
  .grid2 > div + div{border-left:none;border-top:1px solid var(--rule)}
  .defs{columns:1} .wrap{padding:26px 14px 60px}
}
"""

JS = """
(function(){
  var tip=document.getElementById('tip');
  function show(e){var t=e.target.closest('[data-tip]');if(!t){return;}
    tip.textContent=t.getAttribute('data-tip');tip.style.opacity='1';
    tip.style.left=e.clientX+'px';tip.style.top=e.clientY+'px';}
  function hide(){tip.style.opacity='0';}
  document.addEventListener('mousemove',function(e){
    if(e.target.closest('[data-tip]')){show(e);}else{hide();}},{passive:true});
  document.addEventListener('mouseleave',hide);
})();
"""


def _tile(k, v, foot="", pill=None, delta=None, level=None):
    pl = f'<span class="pill {pill[1]}">{esc(pill[0])}</span>' if pill else (delta or "")
    cls = f" {level}" if level else ""
    return (f'<div class="tile{cls}"><div class="k"><span>{esc(k)}</span>{pl}</div>'
            f'<div class="v">{v}</div><div class="f">{foot}</div></div>')


def _u(unit):
    return f'<span class="u">{esc(unit)}</span>'


def _status_dot(ok):
    c = "var(--good)" if ok else "var(--critical)"
    return f'<span class="flag" style="background:{c}"></span>'


def _sec(eyebrow, title, note=""):
    n = f'<p class="note">{note}</p>' if note else ""
    return (f'<section><div class="shead"><span class="eyebrow">{esc(eyebrow)}</span>'
            f'<h2>{esc(title)}</h2>{n}</div>')


def build(m, standalone=True, insight=None):
    cur, base, d, wk = m["current"], m["baseline"], m["deltas"], m["weekly"]
    dora, flow, ai = cur["dora"], cur["flow"], cur["ai"]
    th = m["meta"]["thresholds"]
    unit = flow.get("size_unit") or "個檔"            # files→個檔、lines→行
    slimit = flow.get("size_limit") or th.get("pr_size_healthy_files", 10)
    size_word = f"PR 大小（{unit}）"
    R = {"Elite": "good", "High": "good", "Medium": "warning", "Low": "critical", "—": "muted"}
    LV = {"Medium": "warn", "Low": "crit"}
    demo = m["meta"]["source"] == "mock"
    sc = m["meta"].get("scope") or {}
    title = "{}・工程產出儀表板".format(m["meta"]["team"])
    gen = esc(m["meta"]["generated_at"][:16].replace("T", " "))
    rr = dora["ratings"]

    P = ['<div class="wrap">']
    P.append('<header class="top"><div>'
             f'<h1>{esc(title)}</h1>'
             f'<p class="sub">觀測窗 {m["meta"]["window_days"]} 天（對比前 {base["days"]} 天） · {gen} UTC</p></div>'
             + ('<span class="badge demo">⚠ 示範資料</span>' if demo
                else f'<span class="badge">{esc(sc.get("organization", "Azure DevOps"))}'
                     + (f" / {esc(sc.get('project'))}" if sc.get("project") else "") + '</span>')
             + '</header>')
    # 注意：rr 在上面已經是 dora["ratings"]，這裡不能覆用同一個名字
    rost = m["meta"].get("roster_report") or {}
    if rost.get("missing"):
        P.append('<div class="lede" style="border-bottom:none"><div style="grid-column:1/-1">'
                 '<span class="eyebrow">名單檢查</span>'
                 f'<p>名單裡這幾位在觀測窗內沒有任何合併 PR，確認一下是不是打錯字或換了帳號：'
                 f'<b>{esc("、".join(rost["missing"][:10]))}</b></p></div></div>')

    # ---- 開頭結論 + 需要留意 ----
    al = metrics.alerts(m)
    if al:
        alerts_html = ('<ul class="alerts">'
                       + "".join(f'<li><span><b>{esc(t)}</b>－{esc(x)}</span></li>' for t, x in al[:5])
                       + '</ul>')
    else:
        alerts_html = '<p class="ok">' + _status_dot(True) + '所有護欄指標都在目標範圍內。</p>'
    P.append('<div class="lede">'
             f'<div><span class="eyebrow">一句話結論</span><p>{metrics.headline(m)}</p></div>'
             f'<div><span class="eyebrow">需要留意</span>{alerts_html}</div>'
             '</div>')

    # ---- DORA ----
    P.append(_sec("Delivery Performance", "交付效能・DORA 四大指標",
                  "分級門檻取自 DORA State of DevOps 報告（Elite / High / Medium / Low）。"
                  "這四個指標是團隊層級的，不拆到個人。"))
    P.append('<div class="tiles c4">')
    P.append(_tile("部署頻率", f'{fmt_num(dora["deploy_freq_per_week"])} ' + _u("次/週"),
                   f'期間共 {dora["deploy_count"]} 次成功部署　{delta_html(d["dora.deploy_freq_per_week"])}',
                   pill=(rr["deploy_freq"], R[rr["deploy_freq"]]), level=LV.get(rr["deploy_freq"])))
    P.append(_tile("變更前置時間", fmt_h(dora["lead_time_h"]),
                   f'首次 commit → 上線（中位數）　{delta_html(d["dora.lead_time_h"], True)}',
                   pill=(rr["lead_time"], R[rr["lead_time"]]), level=LV.get(rr["lead_time"])))
    P.append(_tile("變更失敗率", fmt_num(dora["change_failure_rate"]) + _u("%"),
                   f'部署導致失敗或回滾的比例　{delta_html(d["dora.change_failure_rate"], True)}',
                   pill=(rr["change_failure_rate"], R[rr["change_failure_rate"]]),
                   level=LV.get(rr["change_failure_rate"])))
    P.append(_tile("失敗復原時間", fmt_h(dora["mttr_h"]),
                   f'失敗部署 → 恢復（中位數）　{delta_html(d["dora.mttr_h"], True)}',
                   pill=(rr["mttr"], R[rr["mttr"]]), level=LV.get(rr["mttr"])))
    P.append('</div></section>')

    # ---- AI ----
    P.append(_sec("AI Leverage", "AI 協助產出",
                  "依 commit trailer（Co-Authored-By）自動辨識，輔以 PR 標籤。"
                  "架構參考 DX AI Measurement Framework：採用率、吞吐、品質護欄三者必須並看。"))
    guard_ok = bool(ai["ai"] and ai["human"] and (ai["ai"]["cfr"] or 0) <= (ai["human"]["cfr"] or 0) + 2)
    top_tool = ai["by_tool"][0] if ai["by_tool"] else None
    P.append('<div class="tiles c4">')
    P.append(_tile("AI 採用率", fmt_num(ai["adoption_pct"]) + _u("%"),
                   f'{ai["adopters"]} / {flow["active_devs"]} 位有合併 PR 的工程師　{delta_html(d["ai.adoption_pct"])}'))
    P.append(_tile("AI 協助 PR 佔比", fmt_num(ai["ai_pr_pct"]) + _u("%"),
                   f'{ai["ai_pr_count"]} / {flow["pr_merged"]} 個合併 PR　{delta_html(d["ai.ai_pr_pct"])}'))
    P.append(_tile("主要工具",
                   '<span style="font-size:21px">' + (esc(top_tool["tool"]) if top_tool else "—") + '</span>',
                   f'佔 AI PR 的 {top_tool["pct"]}%' if top_tool else "尚未偵測到"))
    P.append(_tile("品質護欄",
                   _status_dot(guard_ok) + '<span style="font-size:21px">'
                   + ("正常" if guard_ok else "需關注") + '</span>',
                   "AI PR 的變更失敗率未高於人工 PR" if guard_ok
                   else "AI PR 失敗率高於人工，先檢視 PR 大小",
                   level=None if guard_ok else "crit"))
    P.append('</div>')

    P.append('<div class="grid2">')
    P.append('<div><h3>每週合併 PR（AI 協助 vs 人工）</h3>'
             '<p class="note">看的是「比例趨勢」，不是絕對數量。</p>'
             + legend([("AI 協助", "var(--s2)"), ("人工", "var(--s1)")])
             + chart_weekly_stack(wk)
             + details_table("以表格檢視", ["週起始", "合併 PR", "AI 協助", "人工", "AI 佔比"],
                             [[r["week_start"], r["merged"], r["ai"], r["human"], f'{r["ai_pct"]}%'] for r in wk])
             + '</div>')
    P.append('<div><h3>AI 工具分佈</h3>'
             '<p class="note">依 trailer 辨識；「Labelled」為只掛標籤、未留 trailer 者。</p>'
             + chart_hbars(ai["by_tool"], "tool", "count", " 個") + '</div>')
    P.append('</div>')

    if ai["ai"] and ai["human"]:
        a, h = ai["ai"], ai["human"]

        def cmp_row(label, k, fmt=lambda v: fmt_num(v), lower_better=True):
            av, hv = a.get(k), h.get(k)
            mark = ""
            if av is not None and hv is not None:
                worse = (av > hv) if lower_better else (av < hv)
                mark = _status_dot(not worse)
            return [label, mark + fmt(av), fmt(hv)]

        P.append('<div class="pad" style="border-top:1px solid var(--rule)">'
                 '<h3>AI PR vs 人工 PR 對照</h3>'
                 '<p class="note" style="margin-bottom:12px">只看吞吐會失真。一併看 PR 大小、審查耗時與失敗率，'
                 '才知道速度是不是借來的。綠點＝AI 表現不差於人工。</p>'
                 + table(["指標", "AI 協助 PR", "人工 PR"], [
                     ["PR 數", a["count"], h["count"]],
                     cmp_row("週期時間（中位）", "cycle_p50", fmt_h),
                     cmp_row(f"PR 大小（中位・{unit}）", "size_p50"),
                     cmp_row("審查耗時（中位）", "review_h_p50", fmt_h),
                     cmp_row("審查留言數（中位）", "comments_p50"),
                     cmp_row("核准後回工率", "rework_pct", lambda v: fmt_num(v, "%")),
                     cmp_row("變更失敗率", "cfr", lambda v: fmt_num(v, "%")),
                 ]) + '</div>')
    P.append('</section>')

    # ---- 流程 ----
    P.append(_sec("Flow", "交付流程・PR 健康度",
                  "週期時間拆解可以直接定位瓶頸：等待審查過長是流程問題，審查過長通常是 PR 太大。"))
    P.append('<div class="tiles c3">')
    P.append(_tile("PR 週期時間", fmt_h(flow["cycle_time_p50"]),
                   f'P75 {fmt_h(flow["cycle_time_p75"])}　{delta_html(d["flow.cycle_time_p50"], True)}'))
    P.append(_tile("每人每週合併 PR", fmt_num(flow["pr_per_dev_week"]),
                   f'{flow["pr_merged"]} 個 PR / {flow["active_devs"]} 人　{delta_html(d["flow.pr_per_dev_week"])}'))
    over = flow["pr_oversize_pct"] > 30
    P.append(_tile(f"PR 大小中位（{unit}）", fmt_num(flow["pr_size_p50"]),
                   f'超過 {slimit}{unit}者佔 {flow["pr_oversize_pct"]}%　'
                   f'{delta_html(d["flow.pr_size_p50"], True)}', level="warn" if over else None))
    cov_ok = flow["review_coverage_pct"] >= th["review_coverage_target_pct"]
    P.append(_tile("審查覆蓋率", fmt_num(flow["review_coverage_pct"]) + _u("%"),
                   f'目標 {th["review_coverage_target_pct"]}%',
                   pill=("達標", "good") if cov_ok else ("未達標", "warning"),
                   level=None if cov_ok else "warn"))
    P.append(_tile("核准後回工率", fmt_num(flow["rework_rate_pct"]) + _u("%"),
                   f'核准後又推 commit 的 PR　{delta_html(d["flow.rework_rate_pct"], True)}'))
    conc = flow["reviewer_concentration_pct"] > 50
    P.append(_tile("審查集中度", fmt_num(flow["reviewer_concentration_pct"]) + _u("%"),
                   "前 2 名審查者承擔的比例，越高越有 bus factor 風險",
                   pill=("偏高", "warning") if conc else ("分散", "good"),
                   level="warn" if conc else None))
    P.append('</div>')

    P.append('<div class="pad"><h3>PR 週期時間拆解（各階段中位數）</h3>'
             '<p class="note" style="margin-bottom:12px">最寬的那一段就是下一個要改的地方。</p>'
             + legend([("撰寫", "var(--s1)"), ("等待審查", "var(--s2)"),
                       ("審查中", "var(--s3)"), ("待合併", "var(--s4)")])
             + chart_breakdown(flow["breakdown"], th["cycle_time_target_hours"])
             + details_table("以表格檢視", ["階段", "中位數"],
                             [["撰寫 Coding（首 commit → 開 PR）", fmt_h(flow["breakdown"]["coding"])],
                              ["等待審查 Pickup（開 PR → 首則審查）", fmt_h(flow["breakdown"]["pickup"])],
                              ["審查中 Review（首則審查 → 核准）", fmt_h(flow["breakdown"]["review"])],
                              ["待合併 Merge（核准 → 合併）", fmt_h(flow["breakdown"]["merge"])]])
             + '</div>')

    P.append('<div class="grid2">')
    P.append(f'<div><h3>PR 大小分佈（{unit}）</h3>'
             f'<p class="note">橘色為超過健康上限 {slimit}{unit}的區間。</p>'
             + chart_hist(flow["size_histogram"], slimit)
             + details_table("以表格檢視", [f"PR 大小（{unit}）", "PR 數"],
                             [[b["label"], b["count"]] for b in flow["size_histogram"]]) + '</div>')
    P.append('<div><h3>每週 PR 週期時間（中位數）</h3>'
             '<p class="note">單位：小時。觀察導入 AI 後交付速度是否真的變快。</p>'
             + chart_line(wk, "cycle_p50", "週期時間", lambda v: f"{v:g}h") + '</div>')
    P.append('</div>')

    if flow["stale_prs"]:
        P.append('<div class="pad" style="border-top:1px solid var(--rule)">'
                 f'<h3>積壓中的 PR（開啟超過 {th["stale_pr_days"]} 天）'
                 f'<span class="pill warning" style="margin-left:9px">{flow["stale_pr_count"]} 個</span></h3>'
                 '<p class="note" style="margin-bottom:12px">最舊的 10 個。積壓的 PR 會持續產生合併衝突與重工。</p>'
                 + table(["PR", "REPO", "標題", "作者", "開啟天數"],
                         [[f'#{sp["id"]}', esc(sp["repo"]), f'<span class="name">{esc(sp["title"])}</span>',
                           f'<span class="name">{esc(sp["author"])}</span>', sp["age_days"]]
                          for sp in flow["stale_prs"]]) + '</div>')
    P.append('</section>')

    # ---- 工作配比 ----
    P.append(_sec("Impact", "工作配比",
                  "DX Core 4 的 Impact 面向：新功能、修 bug、技術債的投入比例。"
                  "產出高但全掃在修 bug 上，不是好訊號。"))
    P.append('<div class="pad">'
             + chart_hbars([{"type": w["type"], "pct": w["pct"]} for w in flow["work_mix"]], "type", "pct", "%")
             + '</div></section>')

    # ---- AI 品質評估 ----
    if insight:
        P.append(ai_section.build(insight, m))

    # ---- 成員 ----
    P.append(_sec("People", "成員視圖",
                  "每一列都同時列出吞吐與護欄欄位。用途是找出「誰需要幫忙」，不是排名。"))
    P.append('<div class="pad"><div class="warnbox" style="margin-bottom:18px">'
             '<b>使用守則：</b>個人層級的 PR 數與 AI 比例<b>不可</b>作為績效考核依據。'
             'DORA 與 SPACE 兩個框架都明確指出，個人產出指標一旦被當成 KPI，'
             '就會被最佳化成「切小 PR、灌數量」而失去訊號（Goodhart 定律）。'
             '這張表的正確用法是：找出週期時間特別長的人去問「卡在哪」，'
             '找出審查量過度集中的人去分擔負載，找出完全沒參與審查的人把他拉進來。</div>')
    rows = []
    for pp in cur["people"]:
        warn = []
        if pp["oversize_pct"] > 30:
            warn.append("PR 偏大")
        if pp["cycle_p50"] and flow["cycle_time_p50"] and pp["cycle_p50"] > flow["cycle_time_p50"] * 1.5:
            warn.append("週期偏長")
        if pp["reviews_given"] == 0:
            warn.append("未參與審查")
        rows.append([
            f'<span class="name">{esc(pp["name"])}</span>',
            f'<span class="name">{esc(pp["team"] or "—")}</span>',
            pp["pr_merged"], pp["pr_per_week"], fmt_h(pp["cycle_p50"]), fmt_num(pp["size_p50"]),
            f'{pp["ai_pct"]}%', pp["reviews_given"], f'{pp["rework_pct"]}%',
            '<span class="name" style="color:var(--muted)">'
            + (esc("・".join(warn)) if warn else "—") + '</span>',
        ])
    P.append(table(["成員", "小組", "合併 PR", "PR/週", "週期時間", f"PR 大小({unit})",
                    "AI 比例", "擔任審查", "回工率", "可留意"], rows))
    P.append('</div></section>')

    # ---- Repo ----
    P.append(_sec("Repositories", "Repository 視圖",
                  "某個 repo 的週期時間或 PR 大小明顯偏高，通常指向測試覆蓋或架構債務。"))
    P.append('<div class="pad">'
             + table(["REPO", "合併 PR", "週期時間", f"PR 大小（中位・{unit}）", "AI 比例"],
                     [[f'<span class="name">{esc(r["repo"])}</span>', r["pr_merged"],
                       fmt_h(r["cycle_p50"]), fmt_num(r["size_p50"]), f'{r["ai_pct"]}%'] for r in cur["repos"]])
             + '</div></section>')

    # ---- 定義 ----
    P.append(_sec("Definitions", "指標定義與出處"))
    P.append('<div class="pad"><div class="defs">'
             '<p><b>部署頻率</b>｜單位時間內成功部署到生產環境的次數。</p>'
             '<p><b>變更前置時間 Lead Time for Changes</b>｜從第一個 commit 到該變更上線的時間中位數。</p>'
             '<p><b>變更失敗率 Change Failure Rate</b>｜導致服務降級或需回滾的部署比例。</p>'
             '<p><b>失敗復原時間</b>｜失敗部署發生到恢復正常的時間中位數（DORA 2023 起取代 MTTR 命名）。</p>'
             '<p><b>PR 週期時間 Cycle Time</b>｜開 PR 到合併，拆成「撰寫／等待審查／審查中／待合併」四段定位瓶頸。</p>'
             '<p><b>等待審查 Pickup Time</b>｜PR 開啟到第一則他人審查留言。這段通常是整條流程最大的浪費。</p>'
             '<p><b>PR 變更行數</b>｜新增＋刪除行數。業界普遍以 200–400 行為健康上限，超過後審查品質明顯下降。</p>'
             '<p><b>核准後回工率</b>｜核准後又推新 commit 的 PR 比例，反映審查時機或需求釐清問題。</p>'
             '<p><b>審查集中度</b>｜前兩名審查者承擔的審查比例，過高代表知識與負載集中（bus factor 風險）。</p>'
             '<p><b>AI 採用率</b>｜期間內至少有一個 AI 協助 PR 的工程師佔比。</p>'
             '<p><b>AI 協助 PR</b>｜commit message 含 <code>Co-Authored-By: Claude / Copilot / Cursor…</code> '
             'trailer，或掛上 ai-assisted 標籤的 PR。</p>'
             '<p><b>已知限制</b>｜未留 trailer 的 AI 使用（例如直接接受編輯器裡的補全）抓不到，採用率會被低估。</p>'
             '<p><b>框架出處</b>｜DORA（State of DevOps）、SPACE 框架、DX Core 4、DX AI Measurement Framework。</p>'
             '</div></div></section>')

    P.append('</div><div id="tip" role="status"></div>')
    page_body = "\n".join(P)

    if not standalone:
        return (f'<title>{esc(title)}</title>\n{FONTS}\n<style>{CSS}{ai_section.CSS}</style>\n'
                f'{page_body}\n<script>{JS}</script>')
    return ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>{FONTS}'
            f'<style>{CSS}{ai_section.CSS}</style></head><body>{page_body}<script>{JS}</script></body></html>')
