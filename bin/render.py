# -*- coding: utf-8 -*-
"""HTML 渲染 — 產生自帶樣式的單檔儀表板（可本機開啟，也可發佈成 Artifact）。

配色採用已驗證的分類調色盤（adjacent CVD ΔE ≥ 8、normal-vision ΔE ≥ 15，
light/dark 兩個模式各自選色）。淺色模式下 aqua/yellow 對比低於 3:1，
依規則一律附「直接標籤 + 表格檢視」作為補償。
"""
import html
import json

# 分類色（slot 1..4）；status 色固定不隨主題變
LIGHT = dict(s1="#2a78d6", s2="#eb6834", s3="#1baf7a", s4="#eda100")
DARK = dict(s1="#3987e5", s2="#d95926", s3="#199e70", s4="#c98500")


def esc(x):
    return html.escape(str(x if x is not None else "—"))


def fmt_h(h):
    """小時 → 人看得懂的字串。"""
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)} 分"
    if h < 48:
        return f"{h:.1f} 小時"
    return f"{h / 24:.1f} 天"


def fmt_num(x, unit=""):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:g}{unit}"
    return f"{x}{unit}"


def delta_html(d, lower_is_better=False):
    if d is None:
        return '<span class="delta muted">— 無基準</span>'
    good = (d < 0) if lower_is_better else (d > 0)
    if abs(d) < 1:
        return '<span class="delta muted">持平</span>'
    arrow = "▲" if d > 0 else "▼"
    cls = "up" if good else "down"
    return f'<span class="delta {cls}">{arrow} {abs(d):g}%</span>'


# ---------- SVG 基本件 ----------

def _grid(y0, y1, w, ticks, scale, fmt=lambda v: f"{v:g}"):
    out = []
    for t in ticks:
        y = scale(t)
        out.append(f'<line class="grid" x1="44" y1="{y:.1f}" x2="{w - 8}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis" x="38" y="{y + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
    return "".join(out)


def _nice_ticks(vmax, n=4):
    """產生好看的刻度：最高刻度一定 >= vmax，且刻度數不超過 n 段。"""
    import math
    if vmax <= 0:
        return [0, 1]
    for k in range(-3, 9):
        for m in (1, 2, 2.5, 5):
            step = m * (10 ** k)
            if math.ceil(vmax / step - 1e-9) <= n:
                cnt = int(math.ceil(vmax / step - 1e-9))
                return [round(i * step, 4) for i in range(cnt + 1)]
    return [0, vmax]


def chart_weekly_stack(rows):
    """每週合併 PR 數，拆 AI 協助 / 人工（堆疊長條）。2 series → 必備圖例。"""
    W, H = 720, 260
    pad_b, pad_t = 34, 16
    vmax = max([r["merged"] for r in rows] + [1])
    ticks = _nice_ticks(vmax)
    top = max(ticks)

    def sy(v):
        return pad_t + (H - pad_t - pad_b) * (1 - v / top)

    bw = (W - 52 - 8) / len(rows)
    bar_w = min(42, bw * 0.62)
    body = [_grid(pad_t, H - pad_b, W, ticks, sy)]
    for i, r in enumerate(rows):
        x = 44 + bw * i + (bw - bar_w) / 2
        y_h = sy(r["human"])
        h_h = (H - pad_b) - y_h
        y_a = sy(r["merged"])
        h_a = max(0, y_h - y_a - 2)            # 2px 表面留白分隔堆疊段
        tip = f'{r["week_start"]} 當週｜合併 {r["merged"]}（AI {r["ai"]}・人工 {r["human"]}）｜AI 佔比 {r["ai_pct"]}%'
        if h_a > 0:
            body.append(f'<rect class="mk" data-tip="{esc(tip)}" x="{x:.1f}" y="{y_a:.1f}" '
                        f'width="{bar_w:.1f}" height="{h_a:.1f}" rx="4" fill="var(--s2)"/>')
        body.append(f'<rect class="mk" data-tip="{esc(tip)}" x="{x:.1f}" y="{y_h:.1f}" '
                    f'width="{bar_w:.1f}" height="{max(0, h_h):.1f}" rx="{0 if h_a > 0 else 4}" fill="var(--s1)"/>')
        body.append(f'<text class="axis" x="{x + bar_w / 2:.1f}" y="{H - 14}" text-anchor="middle">{esc(r["week_start"])}</text>')
        if i == len(rows) - 1:                  # 只在最後一根直接標數字
            body.append(f'<text class="vlabel" x="{x + bar_w / 2:.1f}" y="{sy(r["merged"]) - 7:.1f}" '
                        f'text-anchor="middle">{r["merged"]}</text>')
    body.append(f'<line class="baseline" x1="44" y1="{H - pad_b}" x2="{W - 8}" y2="{H - pad_b}"/>')
    return _svg(W, H, "".join(body))


def chart_breakdown(bd, target_h):
    """PR 週期時間拆解（單條水平堆疊）。段內放直接標籤，對比不足時的補償。"""
    W, H = 720, 112
    segs = [("撰寫 Coding", bd["coding"], "var(--s1)"),
            ("等待審查 Pickup", bd["pickup"], "var(--s2)"),
            ("審查中 Review", bd["review"], "var(--s3)"),
            ("待合併 Merge", bd["merge"], "var(--s4)")]
    total = sum(s[1] for s in segs) or 1
    x0, x1, y, bh = 12, W - 12, 34, 30
    span = x1 - x0
    body, cx = [], x0
    for i, (lbl, v, col) in enumerate(segs):
        w = span * v / total
        gap = 2 if i < len(segs) - 1 else 0
        rx = 4 if i in (0, len(segs) - 1) else 0
        tip = f"{lbl}：{fmt_h(v)}（佔 {100 * v / total:.0f}%）"
        body.append(f'<rect class="mk" data-tip="{esc(tip)}" x="{cx:.1f}" y="{y}" width="{max(0, w - gap):.1f}" '
                    f'height="{bh}" rx="{rx}" fill="{col}"/>')
        if w > 76:
            body.append(f'<text class="seg" x="{cx + (w - gap) / 2:.1f}" y="{y + bh / 2 + 4:.1f}" '
                        f'text-anchor="middle">{fmt_h(v)}</text>')
        if w > 62:                                  # 太窄就不畫段標籤，交給圖例，避免互相撞上
            lx = min(max(cx + (w - gap) / 2, x0 + 36), x1 - 36)
            body.append(f'<text class="axis" x="{lx:.1f}" y="{y + bh + 18:.1f}" '
                        f'text-anchor="middle">{esc(lbl)}</text>')
        cx += w
    if target_h and target_h < total:
        tx = x0 + span * target_h / total
        body.append(f'<line class="target" x1="{tx:.1f}" y1="{y - 10}" x2="{tx:.1f}" y2="{y + bh + 2}"/>')
        body.append(f'<text class="axis" x="{tx:.1f}" y="{y - 15}" text-anchor="middle">目標 {fmt_h(target_h)}</text>')
    return _svg(W, H, "".join(body))


def chart_hist(bins, threshold_label):
    """PR 變更行數分佈。單一數列 → 單一顏色，不做「越大越深」的值階著色。"""
    W, H = 720, 220
    pad_b, pad_t = 34, 16
    vmax = max([b["count"] for b in bins] + [1])
    ticks = _nice_ticks(vmax)
    top = max(ticks)

    def sy(v):
        return pad_t + (H - pad_t - pad_b) * (1 - v / top)

    bw = (W - 52 - 8) / len(bins)
    bar_w = min(58, bw * 0.6)
    body = [_grid(pad_t, H - pad_b, W, ticks, sy)]
    for i, b in enumerate(bins):
        x = 44 + bw * i + (bw - bar_w) / 2
        yv = sy(b["count"])
        over = b["label"] in ("401–800", ">800")
        col = "var(--s2)" if over else "var(--s1)"
        tip = esc("{} 行：{} 個 PR".format(b["label"], b["count"]))
        body.append('<rect class="mk" data-tip="{}" x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" rx="4" fill="{}"/>'
                    .format(tip, x, yv, bar_w, (H - pad_b) - yv, col))
        body.append(f'<text class="axis" x="{x + bar_w / 2:.1f}" y="{H - 14}" text-anchor="middle">{esc(b["label"])}</text>')
        if b["count"]:
            body.append(f'<text class="vlabel" x="{x + bar_w / 2:.1f}" y="{yv - 7:.1f}" text-anchor="middle">{b["count"]}</text>')
    body.append(f'<line class="baseline" x1="44" y1="{H - pad_b}" x2="{W - 8}" y2="{H - pad_b}"/>')
    return _svg(W, H, "".join(body))


def chart_line(rows, key, label, fmt=lambda v: f"{v:g}"):
    """單一數列折線 → 不需圖例（標題已指名）。"""
    W, H = 720, 200
    pad_b, pad_t = 34, 18
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return '<p class="empty">此期間無資料</p>'
    vmax = max(vals)
    ticks = _nice_ticks(vmax)
    top = max(ticks)

    def sy(v):
        return pad_t + (H - pad_t - pad_b) * (1 - v / top)

    step = (W - 52 - 16) / max(1, len(rows) - 1)
    pts, body = [], [_grid(pad_t, H - pad_b, W, ticks, sy, fmt)]
    for i, r in enumerate(rows):
        v = r.get(key)
        if v is None:
            continue
        x, y = 48 + step * i, sy(v)
        pts.append(f"{x:.1f},{y:.1f}")
    body.append(f'<polyline class="line" points="{" ".join(pts)}"/>')
    for i, r in enumerate(rows):
        v = r.get(key)
        if v is None:
            continue
        x, y = 48 + step * i, sy(v)
        tip = esc("{} 起該週｜{} {}".format(r["week_start"], label, fmt(v)))
        body.append('<circle class="mk dot" data-tip="{}" cx="{:.1f}" cy="{:.1f}" r="5"/>'.format(tip, x, y))
        body.append(f'<text class="axis" x="{x:.1f}" y="{H - 14}" text-anchor="middle">{esc(r["week_start"])}</text>')
    last = [r for r in rows if r.get(key) is not None][-1]
    body.append(f'<text class="vlabel" x="{48 + step * (len(rows) - 1):.1f}" y="{sy(last[key]) - 13:.1f}" '
                f'text-anchor="middle">{fmt(last[key])}</text>')
    body.append(f'<line class="baseline" x1="44" y1="{H - pad_b}" x2="{W - 8}" y2="{H - pad_b}"/>')
    return _svg(W, H, "".join(body))


def chart_hbars(items, label_key, value_key, suffix=""):
    """名目類別的水平長條 → 一律單色。"""
    if not items:
        return '<p class="empty">尚無資料</p>'
    W = 720
    row_h, pad = 30, 8
    H = pad * 2 + row_h * len(items)
    vmax = max(i[value_key] for i in items) or 1
    lab_w, body = 150, []
    for i, it in enumerate(items):
        y = pad + row_h * i
        w = (W - lab_w - 70) * it[value_key] / vmax
        body.append(f'<text class="rlabel" x="{lab_w - 10}" y="{y + 19}" text-anchor="end">{esc(it[label_key])}</text>')
        body.append(f'<rect class="mk" data-tip="{esc(f"{it[label_key]}：{it[value_key]}{suffix}")}" x="{lab_w}" y="{y + 6}" '
                    f'width="{max(2, w):.1f}" height="16" rx="4" fill="var(--s1)"/>')
        body.append(f'<text class="vlabel" x="{lab_w + w + 8:.1f}" y="{y + 19}">{it[value_key]}{suffix}</text>')
    return _svg(W, H, "".join(body))


def _svg(w, h, body):
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
            f'role="img" width="100%">{body}</svg>')


def legend(items):
    li = "".join(f'<span class="lg"><i style="background:{c}"></i>{esc(t)}</span>' for t, c in items)
    return f'<div class="legend">{li}</div>'


def table(headers, rows, caption=None):
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    return f'<div class="tw"><table>{cap}<thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def details_table(summary, headers, rows):
    return f'<details class="dt"><summary>{esc(summary)}</summary>{table(headers, rows)}</details>'
