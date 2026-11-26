"""Chart builders (Altair - already bundled with Streamlit, nothing extra to install on Posit Connect).

Pure functions: DataFrame in, Altair chart out, so they can be rendered and tested without Streamlit.
One look for everything: restrained palette, return period on a log axis, money as $2.5M / $250K, a 250-year marker.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from .constants import HIGHLIGHT_RP, PERIL_COLORS, PERIL_COLUMNS, RP_LIST, SERIES_COLORS
from .rules import ep as ep_rules

INK, MUTED, GRID = "#14213D", "#5B6478", "#E6E9F0"
FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"
LABEL_TO_CODE = {label: code for code, label in PERIL_COLUMNS}
MONEY = "'$' + replace(replace(replace(format(datum.value, '.3~s'), 'G', 'B'), 'k', 'K'), 'm', 'm')"
RP_TICKS = [2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 10000]


def fmt_money(v, dec: int = 1) -> str:
    """$2.5M / $250K / $900."""
    if v is None or pd.isna(v):
        return "-"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.{dec}f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.{dec}f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def _finish(chart, height: int = 380, title: str | None = None, width=None):
    if title:
        chart = chart.properties(title=alt.TitleParams(title, anchor="start", fontSize=14, color=INK, font=FONT, offset=10))
    return (chart.properties(height=height, width=width if width else "container", padding={"left": 6, "right": 14, "top": 8, "bottom": 6})
            .configure_view(strokeWidth=0)
            .configure_axis(labelFont=FONT, titleFont=FONT, labelFontSize=11, titleFontSize=11, labelColor=MUTED, titleColor=MUTED,
                            gridColor=GRID, domainColor="#B8BFCC", tickColor="#B8BFCC", titleFontWeight="normal", titlePadding=10)
            .configure_legend(labelFont=FONT, titleFont=FONT, labelFontSize=11, labelColor=INK, orient="bottom", title=None,
                              symbolStrokeWidth=3, symbolType="stroke", padding=4, columnPadding=14)
            .configure_title(font=FONT))


def _palette(labels: list) -> alt.Scale:
    return alt.Scale(domain=labels, range=[PERIL_COLORS.get(LABEL_TO_CODE.get(l, ""), "#8A9BB5") for l in labels])


# ---------------------------------------------------------------- EP curves
def ep_long(cols: list, perspective: str, ep_type: str, layer: str | None = None) -> pd.DataFrame:
    """Long table for charting: one row per peril and return period. Perils with no loss at all are dropped."""
    t = ep_rules.ep_table(cols, perspective, ep_type, layer)
    rows = []
    for _code, label in PERIL_COLUMNS:
        vals = t[label].tolist()
        if not any(v for v in vals if v and v > 1):
            continue
        for rp, prob, v in zip(t["Return Period"], t["Probability"], vals):
            if v is not None and not pd.isna(v):
                rows.append({"Return period": rp, "Probability": prob, "Peril": label, "Loss": float(v)})
    return pd.DataFrame(rows, columns=["Return period", "Probability", "Peril", "Loss"])


def _rp_x(title: str = "Return period (years)"):
    return alt.X("Return period:Q", scale=alt.Scale(type="log", domain=[2, 10000], nice=False),
                 axis=alt.Axis(values=RP_TICKS, format=",d", title=title, grid=True, labelOverlap=True))


def _y(title: str = "Loss (USD)", log: bool = False):
    scale = alt.Scale(type="symlog", constant=1000) if log else alt.Scale(zero=True, nice=True)
    axis = alt.Axis(labelExpr=MONEY, title=title, values=[0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9]) if log else \
        alt.Axis(labelExpr=MONEY, title=title)
    return alt.Y("Loss:Q", scale=scale, axis=axis)


def _marker():
    rule = alt.Chart(pd.DataFrame({"rp": [HIGHLIGHT_RP]})).mark_rule(strokeDash=[5, 4], color="#8791A5", strokeWidth=1.2).encode(
        x=alt.X("rp:Q", scale=alt.Scale(type="log", domain=[2, 10000], nice=False)))
    label = alt.Chart(pd.DataFrame({"rp": [HIGHLIGHT_RP], "t": [f"{HIGHLIGHT_RP}-year"]})).mark_text(
        align="left", baseline="top", dx=5, dy=2, fontSize=11, color="#6B7488", font=FONT).encode(
        x=alt.X("rp:Q", scale=alt.Scale(type="log", domain=[2, 10000], nice=False)), y=alt.value(0), text="t:N")
    return rule + label


def ep_chart(df: pd.DataFrame, title: str | None = None, y_log: bool = False, height: int = 400, width=None):
    """EP curves by peril: single perils thin, All Perils bold navy, 250-year marked, hover for the exact loss."""
    labels = [l for _c, l in PERIL_COLUMNS if l in set(df["Peril"])]
    color = alt.Color("Peril:N", scale=_palette(labels), sort=labels, legend=alt.Legend(symbolStrokeWidth=3))
    tip = [alt.Tooltip("Peril:N"), alt.Tooltip("Return period:Q", format=",d", title="Return period"),
           alt.Tooltip("Probability:Q", format=".2%", title="Annual probability"), alt.Tooltip("Loss:Q", format="$,.0f")]
    base = alt.Chart(df).encode(x=_rp_x(), y=_y(log=y_log), color=color, tooltip=tip)
    thin = base.transform_filter(alt.datum.Peril != "All Perils")
    bold = base.transform_filter(alt.datum.Peril == "All Perils")
    layers = [_marker(),
              thin.mark_line(strokeWidth=2, opacity=0.92, interpolate="monotone"),
              thin.mark_point(filled=True, size=42, opacity=1),
              bold.mark_line(strokeWidth=3.6, interpolate="monotone"),
              bold.mark_point(filled=True, size=80, opacity=1)]
    return _finish(alt.layer(*layers), height, title, width)


def compare_chart(df: pd.DataFrame, title: str | None = None, height: int = 380, width=None):
    """One curve per layer (columns: Return period, Layer, Loss)."""
    layers = list(dict.fromkeys(df["Layer"]))
    color = alt.Color("Layer:N", sort=layers, scale=alt.Scale(domain=layers, range=SERIES_COLORS[:len(layers)]))
    tip = [alt.Tooltip("Layer:N"), alt.Tooltip("Return period:Q", format=",d"), alt.Tooltip("Loss:Q", format="$,.0f")]
    base = alt.Chart(df).encode(x=_rp_x(), y=_y(), color=color, tooltip=tip)
    return _finish(alt.layer(_marker(), base.mark_line(strokeWidth=3, interpolate="monotone"), base.mark_point(filled=True, size=60, opacity=1)), height, title, width)


def compare_long(cols: list, ep_type: str, perspective: str, layers: list) -> pd.DataFrame:
    rows = []
    for ly in layers:
        c = ep_rules.find_column(cols, perspective, ep_type, "YY", ly)
        if not c:
            continue
        for rp, v in zip(RP_LIST, c["values"]):
            if v is not None:
                rows.append({"Return period": rp, "Layer": ly, "Loss": float(v)})
    return pd.DataFrame(rows, columns=["Return period", "Layer", "Loss"])


# ---------------------------------------------------------------- where the expected loss comes from
def pp_frame(stats: pd.DataFrame) -> pd.DataFrame:
    """From ep_rules.stats_table: single-peril pure premium and its share (All Perils column excluded)."""
    row = stats.iloc[0]
    rows = [{"Peril": c, "Pure premium": float(row[c])} for c in stats.columns[2:] if row[c] is not None and not pd.isna(row[c]) and row[c] > 0]
    df = pd.DataFrame(rows, columns=["Peril", "Pure premium"])
    tot = df["Pure premium"].sum()
    df["Share"] = df["Pure premium"] / tot if tot else 0.0
    df["Label"] = [f"{fmt_money(v)}  ({s * 100:.0f}%)" if s >= 0.005 else f"{fmt_money(v)}  (<1%)" for v, s in zip(df["Pure premium"], df["Share"])]
    return df


def pp_chart(df: pd.DataFrame, title: str | None = None, width=None):
    labels = df.sort_values("Pure premium", ascending=False)["Peril"].tolist()
    base = alt.Chart(df).encode(y=alt.Y("Peril:N", sort=labels, axis=alt.Axis(title=None, labelFontSize=12, labelColor=INK, ticks=False, domain=False)))
    bars = base.mark_bar(cornerRadiusEnd=3, height=22).encode(
        x=alt.X("Pure premium:Q", axis=alt.Axis(labelExpr=MONEY, title="Annual average loss (pure premium)", grid=True),
                scale=alt.Scale(domain=[0, float(df["Pure premium"].max()) * 1.35 if len(df) else 1])),
        color=alt.Color("Peril:N", scale=_palette(labels), legend=None),
        tooltip=[alt.Tooltip("Peril:N"), alt.Tooltip("Pure premium:Q", format="$,.0f"), alt.Tooltip("Share:Q", format=".1%")])
    text = base.mark_text(align="left", dx=6, fontSize=11.5, color=INK, font=FONT).encode(x="Pure premium:Q", text="Label:N")
    return _finish(bars + text, max(120, 38 * len(df) + 50), title, width)


# ---------------------------------------------------------------- RDS events
def rds_long(df: pd.DataFrame) -> pd.DataFrame:
    """From the RDS Events frame: events with any loss, one row per measure (ground up and each layer)."""
    loss_cols = [c for c in df.columns if c == "GU_Loss" or c.endswith("_GR")]
    keep = df[df[loss_cols].fillna(0).sum(axis=1) > 0]
    rows = []
    for _, r in keep.iterrows():
        for c in loss_cols:
            if pd.notna(r[c]) and r[c]:
                rows.append({"Event": r["EventName"], "Measure": "Ground up" if c == "GU_Loss" else c[:-3] + " layer", "Loss": float(r[c]),
                             "GU": float(r["GU_Loss"]) if pd.notna(r["GU_Loss"]) else 0.0})
    return pd.DataFrame(rows, columns=["Event", "Measure", "Loss", "GU"])


def rds_chart(df: pd.DataFrame, title: str | None = None, width=None):
    measures = list(dict.fromkeys(df["Measure"]))
    order = df.groupby("Event")["Loss"].max().sort_values(ascending=False).index.tolist()
    colors = ["#0B2545"] + SERIES_COLORS[1:len(measures)]
    chart = alt.Chart(df).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y("Event:N", sort=order, axis=alt.Axis(title=None, labelColor=INK, labelFontSize=12, ticks=False, domain=False)),
        yOffset=alt.YOffset("Measure:N", sort=measures),
        x=alt.X("Loss:Q", axis=alt.Axis(labelExpr=MONEY, title="Event loss (USD)")),
        color=alt.Color("Measure:N", sort=measures, scale=alt.Scale(domain=measures, range=colors), legend=alt.Legend(symbolType="square", symbolStrokeWidth=0)),
        tooltip=[alt.Tooltip("Event:N"), alt.Tooltip("Measure:N"), alt.Tooltip("Loss:Q", format="$,.0f")])
    return _finish(chart, max(150, len(order) * (len(measures) * 14 + 16) + 70), title, width)


# ---------------------------------------------------------------- exposure
def tiv_bar(df: pd.DataFrame, cat: str, title: str | None = None, order: list | None = None, top: int | None = None,
            colors: dict | None = None, width=None):
    """Horizontal bars of TIV by category with 'value  (n locations)' labels. df needs: <cat>, Loc Count, Sum of Location TIV."""
    d = df.rename(columns={"Sum of Location TIV": "TIV", "Loc Count": "Locations"}).copy()
    if top:
        d = d.sort_values("TIV", ascending=False).head(top)
    d["Label"] = [f"{fmt_money(t)}  ·  {int(n)} loc" for t, n in zip(d["TIV"], d["Locations"])]
    sort = order if order else d.sort_values("TIV", ascending=False)[cat].tolist()
    sort = [s for s in sort if s in set(d[cat])]
    color = alt.Color(f"{cat}:N", scale=alt.Scale(domain=list(colors), range=list(colors.values())), legend=None) if colors else alt.value("#2F6DB5")
    base = alt.Chart(d).encode(y=alt.Y(f"{cat}:N", sort=sort, axis=alt.Axis(title=None, labelColor=INK, labelFontSize=12, ticks=False, domain=False, labelLimit=260)))
    bars = base.mark_bar(cornerRadiusEnd=3, height=20).encode(
        x=alt.X("TIV:Q", axis=alt.Axis(labelExpr=MONEY, title="Total insured value"), scale=alt.Scale(domain=[0, float(d["TIV"].max() or 1) * 1.45])),
        color=color, tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip("TIV:Q", format="$,.0f"), alt.Tooltip("Locations:Q", format=",d")])
    text = base.mark_text(align="left", dx=6, fontSize=11, color=INK, font=FONT).encode(x="TIV:Q", text="Label:N")
    return _finish(bars + text, max(90, 32 * len(d) + 48), title, width)


def location_map_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Frame for st.map: bubble size by TIV, colour by share of the largest point."""
    from .rules.locations import map_aggregate
    mp = map_aggregate(df)
    mp = mp.assign(lat=pd.to_numeric(mp["Latitude"], errors="coerce"), lon=pd.to_numeric(mp["Longitude"], errors="coerce")).dropna(subset=["lat", "lon"]).copy()
    mx = float(mp["Location TIV Sum"].max() or 1) if len(mp) else 1.0
    mp["size"] = 250 + 3500 * (mp["Location TIV Sum"] / mx)
    mp["color"] = ["#C8434B" if t / mx >= 0.5 else "#E0A100" if t / mx >= 0.15 else "#2F6DB5" for t in mp["Location TIV Sum"]]
    return mp
