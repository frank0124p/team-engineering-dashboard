# -*- coding: utf-8 -*-
"""AI 品質評估的頁面區塊。與 page.py 共用同一套樣式 token。"""
from render import esc, table, details_table

BAR_W = 150


def _score_bar(val, width=BAR_W):
    """1–5 分的橫條。單一數列 → 單一顏色，不做「分數越高越深」的值階著色。"""
    if val is None:
        return '<span class="na">資料不足</span>'
    pct = (val - 1) / 4 * 100
    return (f'<span class="sbar" title="{val} / 5">'
            f'<span class="sbar-fill" style="width:{pct:.0f}%"></span></span>'
            f'<b class="sval">{val}</b>')


def _conf(cov):
    if cov is None:
        return ("—", "muted")
    if cov >= 0.85:
        return (f"證據涵蓋 {cov:.0%}", "good")
    if cov >= 0.5:
        return (f"證據涵蓋 {cov:.0%}", "warning")
    return (f"證據涵蓋 {cov:.0%}", "critical")


def build(ai, m):
    if not ai:
        return ""
    dims = ai["dimensions"]
    ts = ai.get("team_summary") or {}
    P = []

    P.append('<section><div class="shead"><span class="eyebrow">AI Insight</span>'
             '<h2>產出品質評估</h2>'
             '<p class="note">由 Claude 逐人閱讀該期間的 PR 後給出。'
             f'評分面向共 {len(dims)} 項，每項 1–5 分（3 分＝符合預期）。</p></div>')

    # 使用守則 —— 這段決定這份評分是幫手還是凶器，不可移除
    P.append('<div class="pad"><div class="warnbox">'
             '<b>這份評分怎麼用：</b>它是<b>對話的起點</b>，不是結論，更不是可以直接換算成考績的數字。<br>'
             '每個分數都附了依據的 PR 編號 —— 看分數前先看證據。'
             '如果你不同意某個判斷，那代表模型看到的資料不足以支撐它，'
             '請以你自己的觀察為準，並把這件事記下來當作評估盲點。<br>'
             '<b>三件它做不到的事：</b>看不到 code review 以外的貢獻'
             '（帶新人、救火、跨組協調、寫文件）；看不到任務難度的差異'
             '（有人在清爛攤子，有人在做新功能）；看不到當事人這段期間的處境。'
             '這三件事通常才是績效差異的真正來源。'
             '</div></div>')

    # 團隊概況
    P.append('<div class="tiles c4">')
    P.append(f'<div class="tile"><div class="k"><span>已評分</span></div>'
             f'<div class="v">{ts.get("assessed", 0)}<span class="u"> 位</span></div>'
             f'<div class="f">樣本不足未評分 {ts.get("skipped", 0)} 位</div></div>')
    P.append(f'<div class="tile"><div class="k"><span>團隊中位數</span></div>'
             f'<div class="v">{ts.get("median") or "—"}</div>'
             f'<div class="f">滿分 5，3 分為符合預期</div></div>')
    P.append(f'<div class="tile"><div class="k"><span>評分門檻</span></div>'
             f'<div class="v">{ai["min_prs_for_score"]}<span class="u"> PR</span></div>'
             f'<div class="f">低於此數只給觀察，不給分</div></div>')
    P.append(f'<div class="tile"><div class="k"><span>觀測窗</span></div>'
             f'<div class="v">{ai["window_days"]}<span class="u"> 天</span></div>'
             f'<div class="f">更新於 {esc(ai["generated_at"][:10])}</div></div>')
    P.append('</div>')

    # 面向說明
    P.append('<div class="pad"><h3>評分面向與權重</h3>'
             + table(["面向", "權重", "看什麼"],
                     [[f'<span class="name">{esc(d["label"])}</span>', f'{d["weight"]:.0%}',
                       f'<span class="name">{esc(d["desc"])}</span>'] for d in dims])
             + '<p class="note" style="margin-top:10px">'
               '權重刻意讓「可靠度」與「變更設計」不低於「程式碼品質」—— '
               '寫得漂亮但沒人能審、上線會壞的程式碼，不是高品質產出。</p></div>')

    # 逐人卡片（依姓名排序，不依分數 —— 這不是排行榜）
    for p in sorted(ai["people"], key=lambda x: x["name"]):
        P.append('<div class="pad person">')
        if not p.get("scored"):
            P.append(f'<div class="phead"><h3>{esc(p["name"])}</h3>'
                     f'<span class="pill muted">未評分</span></div>'
                     f'<p class="note">{esc(p.get("note", ""))}</p></div>')
            continue
        label, cls = _conf(p.get("coverage"))
        team = f'　<span class="note">{esc(p["team"])}</span>' if p.get("team") else ""
        P.append(f'<div class="phead"><h3>{esc(p["name"])}{team}</h3>'
                 f'<span class="overall">{p["overall"]}<small> / 5</small></span>'
                 f'<span class="pill {cls}">{esc(label)}</span></div>')
        P.append(f'<p class="psum">{esc(p.get("summary", ""))}</p>')

        rows = []
        for d in dims:
            sc = (p.get("scores") or {}).get(d["key"]) or {}
            ev = "、".join(f"#{e}" for e in sc.get("evidence", [])) or "—"
            rows.append([f'<span class="name">{esc(d["label"])}</span>',
                         _score_bar(sc.get("score")),
                         f'<span class="name reason">{esc(sc.get("reason", ""))}</span>',
                         f'<span class="ev">{esc(ev)}</span>'])
        P.append(table(["面向", "分數", "依據", "證據 PR"], rows))

        for title, key, cls2 in (("做得好的", "strengths", "ok"),
                                 ("可以更好的", "growth_areas", "warn"),
                                 ("1-on-1 可以談", "coaching", "act")):
            items = p.get(key) or []
            if items:
                P.append(f'<div class="lst {cls2}"><span class="eyebrow">{title}</span><ul>'
                         + "".join(f"<li>{esc(i)}</li>" for i in items) + '</ul></div>')
        if p.get("dropped_dimensions"):
            P.append(f'<p class="note">⚠️ 這幾個面向模型舉不出 PR 佐證，已作廢不計入總分：'
                     f'{esc("、".join(p["dropped_dimensions"]))}</p>')
        if p.get("data_gaps"):
            P.append(f'<p class="note">評估盲點：{esc("；".join(p["data_gaps"]))}</p>')
        P.append('</div>')

    P.append('</section>')
    return "\n".join(P)


CSS = """
.person{border-top:1px solid var(--rule)}
.phead{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.phead h3{margin:0}
.overall{font-family:var(--mono);font-size:26px;font-weight:500;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;margin-left:auto}
.overall small{font-size:13px;color:var(--muted);font-weight:400}
.psum{font-size:13.5px;color:var(--ink2);margin:0 0 14px;max-width:78ch;line-height:1.6}
.sbar{display:inline-block;width:92px;height:7px;background:var(--sunk);
  border-radius:4px;overflow:hidden;vertical-align:middle;margin-right:9px}
.sbar-fill{display:block;height:100%;background:var(--s1);border-radius:4px}
.sval{font-family:var(--mono);font-variant-numeric:tabular-nums}
.na{color:var(--muted);font-size:12.5px}
td .reason{white-space:normal;display:block;max-width:46ch;line-height:1.5;
  font-size:12.5px;color:var(--ink2)}
.ev{font-family:var(--mono);font-size:12px;color:var(--muted)}
.lst{margin:14px 0 0;padding-left:13px;border-left:3px solid var(--rule)}
.lst.ok{border-left-color:var(--good)}
.lst.warn{border-left-color:var(--warning)}
.lst.act{border-left-color:var(--s1)}
.lst ul{margin:5px 0 0;padding-left:17px}
.lst li{font-size:13px;color:var(--ink2);margin-bottom:4px;line-height:1.55}
"""
