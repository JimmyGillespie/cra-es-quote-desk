"""Charts for the Results and Exposure pages (Altair, which Streamlit already ships). Pure functions: DataFrame in, chart out, so they
can be rendered and tested without Streamlit. One look throughout: restrained palette, large readable labels, money as $2.5M / $250K,
hover tooltips with the exact figure.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from .constants import HIGHLIGHT_RP, PERIL_COLORS, RP_LIST, SERIES_COLORS

INK, MUTED, GRID = "#14213D", "#5B6478", "#E6E9F0"
FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"
GREY = "#B8BFCC"
LEVEL_COLORS = {"green": "#2E8B57", "amber": "#E0A100", "red": "#C0392B"}
MONEY = "'$' + replace(replace(replace(format(datum.value, '.3~s'), 'G', 'B'), 'k', 'K'), 'm', 'm')"
RP_TICKS = [2, 10, 25, 50, 100, 250, 500, 1000, 10000]
SEQ = ["#EEF3FA", "#9DBBDD", "#2F6DB5", "#0B2545"]


def fmt_money(v, dec: int = 1) -> str:
    """$2.5M / $250K / $900."""
    if v is None or (isinstance(v, float) and v != v):
        return "-"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.{dec}f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.{dec}f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def _finish(chart, height: int = 360, title: str | None = None, width="container", legend_bottom: bool = True):
    if title:
        chart = chart.properties(title=alt.TitleParams(title, anchor="start", fontSize=14, color=INK, font=FONT, offset=10))
    chart = (chart.properties(height=height, width=width, padding={"left": 6, "right": 14, "top": 8, "bottom": 6})
             .configure_view(strokeWidth=0)
             .configure_axis(labelFont=FONT, titleFont=FONT, labelFontSize=12, titleFontSize=12, labelColor=MUTED, titleColor=MUTED,
                             gridColor=GRID, domainColor=GREY, tickColor=GREY, titleFontWeight="normal", titlePadding=10)
             .configure_legend(labelFont=FONT, titleFont=FONT, labelFontSize=12, labelColor=INK, title=None, padding=4, columnPadding=14,
                               **({"orient": "bottom"} if legend_bottom else {"orient": "right"}))
             .configure_title(font=FONT))
    return chart


def _cat_colors(labels: list, fixed: dict | None = None) -> alt.Scale:
    """One colour per category: the fixed ones as given, grey for Unknown / Other, the rest from the palette (never reusing a fixed colour)."""
    fixed = fixed or {}
    free = [c for c in SERIES_COLORS if c not in set(fixed.values())] or SERIES_COLORS
    rng, i = [], 0
    for lab in labels:
        if lab in fixed:
            rng.append(fixed[lab])
        elif str(lab).lower() in ("unknown", "other", "n/a", "not enriched", "not split by cover"):
            rng.append(GREY)
        else:
            rng.append(free[i % len(free)])
            i += 1
    return alt.Scale(domain=labels, range=rng)


# =============================================================== results
def _rp_x(title: str = "Return period (years) - how rare the loss is"):
    return alt.X("Return period:Q", scale=alt.Scale(type="log", domain=[2, 10000], nice=False),
                 axis=alt.Axis(values=RP_TICKS, format=",d", title=title, grid=True, labelOverlap="greedy"))


def _y(title: str = "Loss (USD)", log: bool = False):
    scale = alt.Scale(type="symlog", constant=1000) if log else alt.Scale(zero=True, nice=True)
    axis = alt.Axis(labelExpr=MONEY, title=title, **({"values": [0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9]} if log else {}))
    return alt.Y("Loss:Q", scale=scale, axis=axis)


def _marker():
    r = alt.Chart(pd.DataFrame({"rp": [HIGHLIGHT_RP]})).mark_rule(strokeDash=[5, 4], color="#8791A5", strokeWidth=1.2).encode(
        x=alt.X("rp:Q", scale=alt.Scale(type="log", domain=[2, 10000], nice=False)))
    t = alt.Chart(pd.DataFrame({"rp": [HIGHLIGHT_RP], "t": [f"{HIGHLIGHT_RP}-year"]})).mark_text(
        align="left", baseline="top", dx=5, dy=2, fontSize=11, color="#6B7488", font=FONT).encode(
        x=alt.X("rp:Q", scale=alt.Scale(type="log", domain=[2, 10000], nice=False)), y=alt.value(0), text="t:N")
    return r + t


def ep_curve(df: pd.DataFrame, title: str | None = None, y_log: bool = False, height: int = 420):
    """Loss against return period, one line per peril; All Perils bold; the 250-year marked; hover for the exact loss."""
    names = list(dict.fromkeys(df["Peril"]))
    fixed = {n: PERIL_COLORS.get(c, GREY) for n, c in zip(df["Peril"], df["Code"])}
    color = alt.Color("Peril:N", sort=names, scale=_cat_colors(names, fixed), legend=alt.Legend(symbolType="stroke", symbolStrokeWidth=3))
    tip = [alt.Tooltip("Peril:N"), alt.Tooltip("Return period:Q", format=",d", title="Return period (years)"),
           alt.Tooltip("Probability:Q", format=".2%", title="Chance in any year"), alt.Tooltip("Loss:Q", format="$,.0f")]
    base = alt.Chart(df).encode(x=_rp_x(), y=_y(log=y_log), color=color, tooltip=tip)
    thin, bold = base.transform_filter(alt.datum.Peril != "All Perils"), base.transform_filter(alt.datum.Peril == "All Perils")
    return _finish(alt.layer(_marker(), thin.mark_line(strokeWidth=2, opacity=0.9, interpolate="monotone"), thin.mark_point(filled=True, size=46, opacity=1),
                             bold.mark_line(strokeWidth=3.8, interpolate="monotone"), bold.mark_point(filled=True, size=90, opacity=1)), height, title)


def ladder(rows: list, title: str | None = None, height: int = 300):
    """Columns for the loss at each key return period (rows: [(label, value)]), value written above each bar."""
    df = pd.DataFrame(rows, columns=["Return period", "Loss"]).dropna()
    df["Label"] = df["Loss"].map(fmt_money)
    order = df["Return period"].tolist()
    base = alt.Chart(df).encode(x=alt.X("Return period:N", sort=order, axis=alt.Axis(title=None, labelAngle=0, labelFontSize=12, labelColor=INK)))
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, size=38, color="#2F6DB5").encode(
        y=alt.Y("Loss:Q", axis=alt.Axis(labelExpr=MONEY, title=None)), tooltip=[alt.Tooltip("Return period:N"), alt.Tooltip("Loss:Q", format="$,.0f")])
    txt = base.mark_text(dy=-8, fontSize=12, color=INK, fontWeight=600, font=FONT).encode(y="Loss:Q", text="Label:N")
    return _finish(bars + txt, height, title)


def peril_bars(df: pd.DataFrame, title: str | None = None, value: str = "Average annual loss", height: int | None = None):
    """Horizontal bars by peril with '$ (share)' written at the end. df: Peril, Code, <value>, Share."""
    d = df.copy()
    d["Label"] = [f"{fmt_money(v)}  ({s * 100:.0f}%)" if s >= 0.005 else f"{fmt_money(v)}  (<1%)" for v, s in zip(d[value], d["Share"])]
    order = d.sort_values(value, ascending=False)["Peril"].tolist()
    fixed = {n: PERIL_COLORS.get(c, GREY) for n, c in zip(d["Peril"], d["Code"])}
    base = alt.Chart(d).encode(y=alt.Y("Peril:N", sort=order, axis=alt.Axis(title=None, labelFontSize=13, labelColor=INK, ticks=False, domain=False)))
    bars = base.mark_bar(cornerRadiusEnd=3, height=24).encode(
        x=alt.X(f"{value}:Q", axis=alt.Axis(labelExpr=MONEY, title=value), scale=alt.Scale(domain=[0, float(d[value].max()) * 1.4])),
        color=alt.Color("Peril:N", scale=_cat_colors(order, fixed), legend=None),
        tooltip=[alt.Tooltip("Peril:N"), alt.Tooltip(f"{value}:Q", format="$,.0f"), alt.Tooltip("Share:Q", format=".1%")])
    txt = base.mark_text(align="left", dx=6, fontSize=12, color=INK, font=FONT).encode(x=f"{value}:Q", text="Label:N")
    return _finish(bars + txt, height or max(130, 44 * len(d) + 56), title)


def layer_compare(df: pd.DataFrame, title: str | None = None, height: int = 380):
    layers = list(dict.fromkeys(df["Layer"]))
    color = alt.Color("Layer:N", sort=layers, scale=alt.Scale(domain=layers, range=SERIES_COLORS[:len(layers)]), legend=alt.Legend(symbolType="stroke", symbolStrokeWidth=3))
    tip = [alt.Tooltip("Layer:N"), alt.Tooltip("Return period:Q", format=",d"), alt.Tooltip("Loss:Q", format="$,.0f")]
    base = alt.Chart(df).encode(x=_rp_x(), y=_y(), color=color, tooltip=tip)
    return _finish(alt.layer(_marker(), base.mark_line(strokeWidth=3, interpolate="monotone"), base.mark_point(filled=True, size=60, opacity=1)), height, title)


def rds_bars(df: pd.DataFrame, title: str | None = None):
    """Event losses: one group of bars per event. df: Event, Measure, Loss."""
    measures = list(dict.fromkeys(df["Measure"]))
    order = df.groupby("Event")["Loss"].max().sort_values(ascending=False).index.tolist()
    colors = ["#0B2545"] + SERIES_COLORS[1:len(measures)]
    ch = alt.Chart(df).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y("Event:N", sort=order, axis=alt.Axis(title=None, labelColor=INK, labelFontSize=12, ticks=False, domain=False, labelLimit=260)),
        yOffset=alt.YOffset("Measure:N", sort=measures), x=alt.X("Loss:Q", axis=alt.Axis(labelExpr=MONEY, title="Event loss (USD)")),
        color=alt.Color("Measure:N", sort=measures, scale=alt.Scale(domain=measures, range=colors), legend=alt.Legend(symbolType="square", symbolStrokeWidth=0)),
        tooltip=[alt.Tooltip("Event:N"), alt.Tooltip("Measure:N"), alt.Tooltip("Loss:Q", format="$,.0f")])
    return _finish(ch, max(150, len(order) * (len(measures) * 14 + 16) + 70), title)


# =============================================================== exposure
def donut(df: pd.DataFrame, label: str = "label", value: str = "TIV", title: str | None = None, center: str | None = None, height: int = 330,
          fixed: dict | None = None):
    """Donut with the share on each slice that is big enough to read, the legend beside it, and an optional centre note."""
    d = df[[label, value]].copy()
    d = d[d[value] > 0]
    tot = float(d[value].sum()) or 1.0
    d["Share"] = d[value] / tot
    d["Pct"] = d["Share"].map(lambda s: f"{s * 100:.0f}%" if s >= 0.06 else "")
    d["Amount"] = d[value].map(fmt_money)
    order = d[label].tolist()
    base = alt.Chart(d).encode(theta=alt.Theta(f"{value}:Q", stack=True), order=alt.Order("Share:Q", sort="descending"),
                               color=alt.Color(f"{label}:N", sort=order, scale=_cat_colors(order, fixed), legend=alt.Legend(orient="right", labelLimit=190, symbolType="circle")))
    arc = base.mark_arc(innerRadius=56, outerRadius=102, stroke="white", strokeWidth=1.5).encode(
        tooltip=[alt.Tooltip(f"{label}:N", title="Category"), alt.Tooltip("Amount:N", title="Value"), alt.Tooltip("Share:Q", format=".1%")])
    txt = base.mark_text(radius=121, fontSize=12, fontWeight=600, color=INK, font=FONT).encode(text="Pct:N")
    layers = [arc, txt]
    if center:
        layers.append(alt.Chart(pd.DataFrame({"t": [center]})).mark_text(fontSize=16, fontWeight=700, color=INK, font=FONT).encode(text="t:N"))
    return _finish(alt.layer(*layers), height, title, legend_bottom=False)


def hbar(df: pd.DataFrame, label: str = "label", value: str = "TIV", title: str | None = None, note: str | None = None, color: str = "#2F6DB5",
         order: list | None = None, height: int | None = None):
    """Horizontal bars with '$ (share)' at the end. df needs [label, value, Share]; `note` is an optional extra column shown in the tooltip."""
    d = df.copy()
    d["Text"] = [f"{fmt_money(v)}  ({s * 100:.0f}%)" if s >= 0.005 else f"{fmt_money(v)}  (<1%)" for v, s in zip(d[value], d["Share"])]
    sort = order if order else d.sort_values(value, ascending=False)[label].tolist()
    tips = [alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format="$,.0f"), alt.Tooltip("Share:Q", format=".1%")] + ([alt.Tooltip(f"{note}:Q", format=",d")] if note and note in d else [])
    base = alt.Chart(d).encode(y=alt.Y(f"{label}:N", sort=sort, axis=alt.Axis(title=None, labelColor=INK, labelFontSize=12, ticks=False, domain=False, labelLimit=250)))
    bars = base.mark_bar(cornerRadiusEnd=3, height=20, color=color).encode(
        x=alt.X(f"{value}:Q", axis=alt.Axis(labelExpr=MONEY, title=None), scale=alt.Scale(domain=[0, float(d[value].max() or 1) * 1.5])), tooltip=tips)
    txt = base.mark_text(align="left", dx=6, fontSize=11.5, color=INK, font=FONT).encode(x=f"{value}:Q", text="Text:N")
    return _finish(bars + txt, height or max(90, 31 * len(d) + 40), title)


def column(df: pd.DataFrame, label: str = "label", value: str = "TIV", order: list | None = None, title: str | None = None, color: str = "#2F6DB5", height: int = 260):
    """Vertical columns (ordered categories such as year built or storeys) with the value written above."""
    d = df.copy()
    d["Text"] = d[value].map(fmt_money)
    sort = order or d[label].tolist()
    base = alt.Chart(d).encode(x=alt.X(f"{label}:N", sort=sort, axis=alt.Axis(title=None, labelAngle=0, labelColor=INK, labelFontSize=12)))
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, color=color).encode(
        y=alt.Y(f"{value}:Q", axis=alt.Axis(labelExpr=MONEY, title=None)),
        tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format="$,.0f"), alt.Tooltip("Locations:Q", format=",d"), alt.Tooltip("Share:Q", format=".1%")])
    txt = base.mark_text(dy=-7, fontSize=11.5, color=INK, font=FONT).encode(y=f"{value}:Q", text="Text:N")
    return _finish(bars + txt, height, title)


def heat(g: pd.DataFrame, title: str | None = None, height: int | None = None, row_title: str = "", col_title: str = ""):
    """Heat map of TIV by two categories (the output of expo.matrix): shade is the value, the figure is written in the box."""
    rows, cols = g.attrs.get("row_order") or list(dict.fromkeys(g["row"])), g.attrs.get("col_order") or list(dict.fromkeys(g["col"]))
    d = g.copy()
    d["Text"] = d["TIV"].map(lambda v: fmt_money(v) if v > 0 else "")
    thr = float(d["TIV"].max() or 1) * 0.55
    base = alt.Chart(d).encode(x=alt.X("col:N", sort=cols, axis=alt.Axis(title=col_title or None, labelAngle=0, labelColor=INK, labelFontSize=11, orient="top")),
                               y=alt.Y("row:N", sort=rows, axis=alt.Axis(title=row_title or None, labelColor=INK, labelFontSize=11, labelLimit=230)))
    box = base.mark_rect(stroke="white", strokeWidth=2, cornerRadius=2).encode(
        color=alt.Color("TIV:Q", scale=alt.Scale(range=SEQ), legend=None),
        tooltip=[alt.Tooltip("row:N", title=row_title or "Row"), alt.Tooltip("col:N", title=col_title or "Column"), alt.Tooltip("TIV:Q", format="$,.0f"), alt.Tooltip("Locations:Q", format=",d")])
    txt = base.mark_text(fontSize=11, font=FONT).encode(text="Text:N", color=alt.condition(alt.datum.TIV > thr, alt.value("white"), alt.value(INK)))
    return _finish(box + txt, height or max(120, 34 * len(rows) + 60), title)


def pareto(curve: pd.DataFrame, marks: dict | None = None, title: str | None = None, height: int = 320):
    """Cumulative share of value against share of locations (biggest first). The further it bows above the dotted line, the more concentrated."""
    x = alt.X("Share of locations:Q", axis=alt.Axis(format="%", title="Share of locations (largest first)"), scale=alt.Scale(domain=[0, 1]))
    y = alt.Y("Share of TIV:Q", axis=alt.Axis(format="%", title="Share of total value"), scale=alt.Scale(domain=[0, 1]))
    area = alt.Chart(curve).mark_area(color="#2F6DB5", opacity=0.14, interpolate="monotone").encode(x=x, y=y)
    line = alt.Chart(curve).mark_line(color="#0B2545", strokeWidth=3, interpolate="monotone").encode(x=x, y=y, tooltip=[
        alt.Tooltip("Share of locations:Q", format=".1%"), alt.Tooltip("Share of TIV:Q", format=".1%")])
    diag = alt.Chart(pd.DataFrame({"a": [0, 1], "b": [0, 1]})).mark_line(strokeDash=[4, 4], color=GREY).encode(x="a:Q", y="b:Q")
    layers = [area, diag, line]
    if marks:
        m = pd.DataFrame(marks)
        layers.append(alt.Chart(m).mark_point(filled=True, size=90, color="#C8434B").encode(x="x:Q", y="y:Q", tooltip=[alt.Tooltip("label:N", title="Point")]))
        layers.append(alt.Chart(m).mark_text(align="left", dx=8, dy=-8, fontSize=11.5, color=INK, font=FONT, fontWeight=600).encode(x="x:Q", y="y:Q", text="label:N"))
    return _finish(alt.layer(*layers), height, title)


def score_bars(rows: list, title: str | None = None):
    """Data-quality checks as bars out of 100%, coloured green / amber / red. rows: dicts with check, pct_tiv, good, bad, level."""
    d = pd.DataFrame(rows)
    d["one"] = 1.0
    d["Text"] = [f"{p * 100:.0f}% of value" + (f"  ·  {b} to check" if b else "") for p, b in zip(d["pct_tiv"], d["bad"])]
    order = d["check"].tolist()
    base = alt.Chart(d).encode(y=alt.Y("check:N", sort=order, axis=alt.Axis(title=None, labelColor=INK, labelFontSize=13, ticks=False, domain=False, labelLimit=320)))
    track = base.mark_bar(height=18, color="#EEF1F6", cornerRadiusEnd=4).encode(x=alt.X("one:Q", axis=None, scale=alt.Scale(domain=[0, 1.9])))
    fill = base.mark_bar(height=18, cornerRadiusEnd=4).encode(
        x=alt.X("pct_tiv:Q", axis=None, scale=alt.Scale(domain=[0, 1.9])), color=alt.Color("level:N", scale=alt.Scale(domain=list(LEVEL_COLORS), range=list(LEVEL_COLORS.values())), legend=None),
        tooltip=[alt.Tooltip("check:N", title="Check"), alt.Tooltip("pct_tiv:Q", format=".1%", title="Share of value"), alt.Tooltip("good:Q", title="Pass"), alt.Tooltip("bad:Q", title="To check")])
    txt = base.mark_text(align="left", dx=6, fontSize=12, color=INK, font=FONT).encode(x=alt.X("pct_tiv:Q", scale=alt.Scale(domain=[0, 1.9])), text="Text:N")
    return _finish(alt.layer(track, fill, txt), max(120, 40 * len(d) + 30), title)
