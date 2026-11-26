"""CRA report workbook (.xlsx, macro-free) - rebuilds the tabs of CRA_E&S_Template.xlsm.

Tabs: Review | Account | Exposure Summary | EP GrUp | <one tab per layer> | RDS Events | Locations | Map | Results | Meta |
      RMS Disclaimer.  EP tabs keep the template's cell positions (AEP rows 8-19, statistics 21-24, OEP 31-42).

Values are written as numbers, not INDEX/MATCH formulas: the file is e-mailed and previewed outside Excel, where
uncalculated formulas show as blank. The Results tab holds the full source data. 'Review' replaces the AI Summary tab:
same layout and colours, but every line comes from a rule (see cra/rules/review.py) - no AI.
"""
from __future__ import annotations

import io
import math
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference, ScatterChart, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.series import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.image import Image as XlImage
from openpyxl.drawing.line import LineProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from ..constants import DATA_DIR, HIGHLIGHT_RP, PERIL_COLORS, PERIL_COLUMNS, RP_LIST, TIV_DELTA_ALERT, TIV_DELTA_WARN
from ..rules import ep as ep_rules
from ..rules import locations as loc_rules
from ..rules import matching
from ..rules.layers import unique_sheet_names
from ..rules.rds import annotate

BODY = "Aptos Narrow"
TITLE_COLOR = "156082"
HILITE = PatternFill("solid", fgColor="C1E4F5")
HEAD = PatternFill("solid", fgColor="0066CC")
BAND = PatternFill("solid", fgColor="F2F2F2")
THIN, MED = Side(style="thin"), Side(style="medium")
GREY = Side(style="thin", color="C8C8C8")
NUM = '_-* #,##0_-;\\-* #,##0_-;_-* "-"??_-;_-@_-'
CV_FMT = "0.00"
MONEY_AXIS = '$#,##0.0,,"M"'


def _font(**kw):
    return Font(name=kw.pop("name", BODY), size=kw.pop("size", 11), **kw)


def _box(ws, r1, c1, r2, c2):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(r, c).border = Border(left=MED if c == c1 else THIN, right=MED if c == c2 else THIN,
                                          top=MED if r == r1 else THIN, bottom=MED if r == r2 else THIN)


def _nz(v):
    return 0 if v is None or (isinstance(v, float) and pd.isna(v)) else v


# ---------------------------------------------------------------- EP tabs
def _write_ep_block(ws, top: int, ep_type: str, perspective: str, cols: list, layer: str | None, first_data_row: int):
    ws.cell(top, 2, ep_type).font = _font(bold=True)
    hdr = top + 1
    ws.cell(hdr, 2, "Probability").font = _font(bold=True)
    ws.cell(hdr, 3, "Return Period").font = _font(bold=True)
    tbl = ep_rules.ep_table(cols, perspective, ep_type, layer)
    for j, (_c, label) in enumerate(PERIL_COLUMNS):
        ws.cell(hdr, 4 + j, label).font = _font(bold=True)
    for i, rp in enumerate(RP_LIST):
        r = first_data_row + i
        ws.cell(r, 2, tbl.iloc[i]["Probability"])
        ws.cell(r, 3, rp)
        for j, (_c, label) in enumerate(PERIL_COLUMNS):
            ws.cell(r, 4 + j, _nz(tbl.iloc[i][label])).number_format = NUM
        if rp == HIGHLIGHT_RP:
            for c in range(2, 4 + len(PERIL_COLUMNS)):
                ws.cell(r, c).fill = HILITE
                ws.cell(r, c).font = _font(bold=True)
    _box(ws, hdr, 2, first_data_row + len(RP_LIST) - 1, 3 + len(PERIL_COLUMNS))


def _write_stats(ws, row: int, perspective: str, cols: list, layer: str | None):
    st = ep_rules.stats_table(cols, perspective, layer)
    ws.cell(row, 2, "Statistics")
    ws.cell(row, 4, "All Perils").font = _font(bold=True)
    for j, (_c, label) in enumerate([x for x in PERIL_COLUMNS if x[0] != "YY"]):
        ws.cell(row, 5 + j, label).font = _font(bold=True)
    for i, name in enumerate(["Pure Premium:", "Std Dev:", "CV:"]):
        r = row + 1 + i
        ws.cell(r, 2, name).font = _font(bold=(i == 0))
        for j, col in enumerate(st.columns[1:]):
            cell = ws.cell(r, 4 + j, _nz(st.iloc[i][col]))
            cell.number_format = CV_FMT if name == "CV:" else NUM
            if i == 0:
                cell.font = _font(bold=True)
        if i == 0:
            for c in range(2, 4 + len(PERIL_COLUMNS)):
                ws.cell(r, c).fill = HILITE
    _box(ws, row, 2, row + 3, 3 + len(PERIL_COLUMNS))


def _grid():
    return ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill="E6E9F0", w=9525)))


def _ep_chart(ws, title: str, first_row: int, cols: list, perspective: str, layer: str | None):
    """AEP curves: scatter with a LOG return-period axis (the template used a text axis in reverse order)."""
    tbl = ep_rules.ep_table(cols, perspective, "AEP", layer)
    ch = ScatterChart()
    ch.title, ch.style, ch.scatterStyle = title, 2, "lineMarker"
    ch.height, ch.width = 11.5, 22
    ch.x_axis.title, ch.y_axis.title = "Return period (years)", "Loss (USD)"
    ch.x_axis.scaling.logBase, ch.x_axis.scaling.min, ch.x_axis.scaling.max = 10, 1, 10000
    ch.x_axis.number_format, ch.y_axis.number_format = "#,##0", MONEY_AXIS
    ch.x_axis.majorGridlines, ch.y_axis.majorGridlines = _grid(), _grid()
    ch.x_axis.delete = ch.y_axis.delete = False
    ch.legend.position = "b"
    last = first_row + len(RP_LIST) - 1
    xs = Reference(ws, min_col=3, min_row=first_row, max_row=last)
    n = 0
    for j, (code, label) in enumerate(PERIL_COLUMNS):
        if not any(v for v in tbl[label] if v and v > 1):
            continue
        s = Series(Reference(ws, min_col=4 + j, min_row=first_row, max_row=last), xs, title=label)
        color = PERIL_COLORS[code].lstrip("#")
        s.marker.symbol, s.marker.size = "circle", 8 if code == "YY" else 6
        s.marker.graphicalProperties = GraphicalProperties(solidFill=color)
        s.marker.graphicalProperties.line.solidFill = color
        s.graphicalProperties = GraphicalProperties()
        s.graphicalProperties.line.solidFill = color
        s.graphicalProperties.line.width = 44450 if code == "YY" else 25400
        s.smooth = False
        ch.series.append(s)
        n += 1
    if n:
        ws.add_chart(ch, "N4")


def _pp_chart(ws, stat_row: int, cols: list, perspective: str, layer: str | None):
    """Annual average loss by peril (pure premium), one coloured bar per peril."""
    st = ep_rules.stats_table(cols, perspective, layer)
    row = st.iloc[0]
    live = [(j, c) for j, c in enumerate(st.columns[2:]) if row[c] is not None and not pd.isna(row[c]) and row[c] > 0]
    if not live:
        return
    ch = BarChart()
    ch.type, ch.style, ch.title = "bar", 2, "Annual average loss by peril"
    ch.height, ch.width = 7.5, 22
    ch.legend = None
    ch.y_axis.number_format, ch.y_axis.majorGridlines = '$#,##0,"K"', _grid()
    ch.x_axis.delete = ch.y_axis.delete = False
    ch.x_axis.scaling.orientation = "maxMin"
    first, last = 5, 4 + len(st.columns[2:])
    data = Reference(ws, min_col=first, max_col=last, min_row=stat_row + 1)
    cats = Reference(ws, min_col=first, max_col=last, min_row=stat_row)
    s = Series(data, title="Pure premium")
    s.graphicalProperties = GraphicalProperties(solidFill="2F6DB5")
    peril_codes = [c for c, _l in PERIL_COLUMNS if c != "YY"]
    for j in range(len(peril_codes)):
        pt = DataPoint(idx=j)
        pt.graphicalProperties = GraphicalProperties(solidFill=PERIL_COLORS[peril_codes[j]].lstrip("#"))
        s.dPt.append(pt)
    s.dLbls = DataLabelList()
    s.dLbls.showVal, s.dLbls.showSerName, s.dLbls.showCatName, s.dLbls.showLegendKey = True, False, False, False
    s.dLbls.numFmt = '$#,##0'
    ch.series.append(s)
    ch.set_categories(cats)
    ws.add_chart(ch, "N28")


def _ep_sheet(ws, perspective: str, cols: list, layer: str | None):
    ws.sheet_view.showGridLines = False
    is_gu = perspective == "GU"
    ws["B1"], ws["C1"] = "Perspective", "Ground Up Loss Overview" if is_gu else "Gross Loss Overview"
    for a in ("B1", "C1"):
        ws[a].font = _font(name="Arial", bold=True, color=TITLE_COLOR)
    if not is_gu:
        ws["B2"], ws["C2"] = "Layer", layer
        ws["B2"].font = _font(name="Arial", bold=True, color=TITLE_COLOR)
    ws["B3"] = perspective
    _write_ep_block(ws, 4, "AEP", perspective, cols, layer, 8)
    _write_stats(ws, 21, perspective, cols, layer)
    _write_ep_block(ws, 27, "OEP", perspective, cols, layer, 31)
    for col, w in {"A": 3, "B": 12.4, "C": 13.4, "D": 15, "E": 15, "F": 15, "G": 15, "H": 15, "I": 15, "J": 15, "K": 15}.items():
        ws.column_dimensions[col].width = w
    for r in (6, 7, 29, 30):
        ws.row_dimensions[r].hidden = True
    _ep_chart(ws, "AEP Ground Up Loss" if is_gu else f"AEP Gross Loss - {layer}", 8, cols, perspective, layer)
    _pp_chart(ws, 21, cols, perspective, layer)


# ---------------------------------------------------------------- summary tables
def _summary_table(ws, row: int, col: int, df: pd.DataFrame) -> int:
    nested = list(df.columns[:2]) == ["Admin1", "Admin2"]
    heads = (["State / County"] if nested else [str(df.columns[0])]) + ["Loc Count", "Sum of Location TIV"]
    for j, h in enumerate(heads):
        c = ws.cell(row, col + j, h)
        c.font, c.alignment = _font(bold=True), Alignment(horizontal="left" if j == 0 else "right")
    r = row + 1
    if nested:
        for state, g in df.groupby("Admin1", sort=True):
            ws.cell(r, col, state).font = _font(bold=True)
            ws.cell(r, col + 1, int(g["Loc Count"].sum())).font = _font(bold=True)
            c = ws.cell(r, col + 2, float(g["Sum of Location TIV"].sum()))
            c.font, c.number_format = _font(bold=True), "#,##0"
            r += 1
            for _, x in g.sort_values("Admin2").iterrows():
                ws.cell(r, col, "   " + str(x["Admin2"]))
                ws.cell(r, col + 1, int(x["Loc Count"]))
                ws.cell(r, col + 2, float(x["Sum of Location TIV"])).number_format = "#,##0"
                r += 1
    else:
        for _, x in df.iterrows():
            ws.cell(r, col, str(x.iloc[0]))
            ws.cell(r, col + 1, int(x["Loc Count"]))
            ws.cell(r, col + 2, float(x["Sum of Location TIV"])).number_format = "#,##0"
            r += 1
    ws.cell(r, col, "Total Result").font = _font(bold=True)
    ws.cell(r, col + 1, int(df["Loc Count"].sum())).font = _font(bold=True)
    c = ws.cell(r, col + 2, float(df["Sum of Location TIV"].sum()))
    c.font, c.number_format = _font(bold=True), "#,##0"
    _box(ws, row, col, r, col + 2)
    ws.column_dimensions[get_column_letter(col)].width = 26 if nested else 22
    ws.column_dimensions[get_column_letter(col + 1)].width = 13
    ws.column_dimensions[get_column_letter(col + 2)].width = 23
    return r + 1


def _exposure_summary(ws, df: pd.DataFrame):
    ws.sheet_view.showGridLines = False
    s = loc_rules.exposure_summaries(df)
    end = _summary_table(ws, 2, 2, s["Account"])
    _summary_table(ws, max(end + 2, 11), 2, s["Wind Tier"])
    col = 6
    for key in ("State / County", "Construction", "Occupancy", "Geocoding Resolution", "Year Built Band", "Stories Band"):
        _summary_table(ws, 2, col, s[key])
        col += 4


# ---------------------------------------------------------------- Review (replaces the AI Summary tab)
LEVEL_STYLE = {"red": ("FFC7CE", "9C0006", "\u274c"), "amber": ("FFEB9C", "9C6500", "\u26a0\ufe0f"),
               "good": ("C6EFCE", "006100", "\u2705"), "info": ("DAEEF3", "1F3864", "\u2139\ufe0f")}
SECTIONS = [("red", "Red flags - must fix"), ("amber", "Amber flags - need clarification"), ("good", "Good"), ("info", "Notes")]


def _review_sheet(ws, sub: dict, review: dict):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 25, 80
    f10 = lambda **k: Font(name="Calibri", size=10, **k)
    thin = Side(style="thin", color="B4B4B4")

    def banner(r, text, fill="1F3864", color="FFFFFF", size=12, bold=True, italic=False, h=25):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        c = ws.cell(r, 1, text)
        c.font = Font(name="Calibri", size=size, bold=bold, italic=italic, color=color)
        c.fill = PatternFill("solid", fgColor=fill)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = h

    r = 1
    banner(r, f"CRA Review - {sub.get('account_name') or sub.get('exposure_name', '')}")
    r += 1
    banner(r, "Rule-based review of the model output and the location schedule (no AI). Online due diligence is not included. "
              "Verify independently; this is not professional advice.", fill="D9D9D9", color="000000", size=9, bold=False, italic=True, h=28)
    r += 2
    banner(r, "Account snapshot")
    for k, v in review["snapshot"]:
        r += 1
        a, b = ws.cell(r, 1, k), ws.cell(r, 2, v)
        a.font, b.font = f10(bold=True), f10()
        a.fill = PatternFill("solid", fgColor="D6E4F0")
        for c in (a, b):
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        ws.row_dimensions[r].height = max(15, 13.5 * math.ceil(len(str(v)) / 95))
    r += 1
    banner(r, review["headline"], fill="F2F2F2", color="1F3864", size=11, h=22)
    for level, title in SECTIONS:
        items = [f for f in review["findings"] if f["level"] == level]
        if not items:
            continue
        fill, color, mark = LEVEL_STYLE[level]
        r += 2
        banner(r, title, fill=fill, color=color, size=11, h=22)
        for i, f in enumerate(items, 1):
            r += 1
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
            c = ws.cell(r, 1, f"{i}. {mark} [{f['area']}] {f['text']}" if level in ("red", "amber") else f"{mark} [{f['area']}] {f['text']}")
            c.font = Font(name="Calibri", size=10, color=color)
            c.fill = PatternFill("solid", fgColor=fill)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = max(15, 13.5 * math.ceil(len(c.value) / 112))


# ---------------------------------------------------------------- other tabs
def _account_sheet(ws, sub: dict, recon: dict | None, records: list, warnings: list, generated: str, timings: dict, layer_order: list):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 28, 58
    for c, w in zip("CDEFGH", (14, 26, 12, 12, 14, 14)):
        ws.column_dimensions[c].width = w
    ws["A1"] = "CRA E&S - Account"
    ws["A1"].font = _font(name="Arial", bold=True, size=14, color=TITLE_COLOR)
    r = 3
    for k, v in [("EDM / Exposure", sub.get("exposure_name")), ("Exposure ID", sub.get("exposure_id")), ("Account", sub.get("account_name")),
                 ("Underwriter", sub.get("underwriter")), ("Priority", sub.get("priority")), ("Quote terms", sub.get("comments")),
                 ("E-mail subject", sub.get("email_subject")), ("Generated", generated)]:
        ws.cell(r, 1, k).font = _font(bold=True)
        ws.cell(r, 2, v).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    r += 1
    ws.cell(r, 1, "TIV check").font = _font(bold=True, color=TITLE_COLOR)
    r += 1
    if recon:
        for k, v, f in [("TIV Reported", recon.get("tiv_reported"), "#,##0"), ("TIV Modelled", recon.get("tiv_modelled"), "#,##0"),
                        ("Delta", recon.get("delta"), "0.0%"), ("Locations", recon.get("locations"), "#,##0")]:
            ws.cell(r, 1, k)
            c = ws.cell(r, 2, v)
            c.number_format, c.alignment = f, Alignment(horizontal="left")
            if k == "Delta" and v is not None:
                if abs(v) >= TIV_DELTA_ALERT:
                    c.fill = PatternFill("solid", fgColor="F8CBAD")
                elif abs(v) >= TIV_DELTA_WARN:
                    c.fill = PatternFill("solid", fgColor="FFE699")
            r += 1
    steps = [("Locations", "locations"), ("Hazards", "hazards"), ("EP and RDS results", "results")]
    if any(k in timings for _l, k in steps):
        r += 1
        ws.cell(r, 1, "Run timings (seconds)").font = _font(bold=True, color=TITLE_COLOR)
        for label, key in steps:
            if key in timings:
                r += 1
                ws.cell(r, 1, label)
                ws.cell(r, 2, timings[key]).alignment = Alignment(horizontal="left")
        r += 1
    r += 1
    ws.cell(r, 1, "Analyses used").font = _font(bold=True, color=TITLE_COLOR)
    r += 1
    for j, h in enumerate(["Layer", "Analysis name", "Analysis ID", "Peril", "Peril code", "Engine", "Currency"]):
        c = ws.cell(r, 1 + j, h)
        c.font, c.fill = _font(bold=True, color="FFFFFF"), HEAD
    for a in records:
        r += 1
        for j, v in enumerate([a["layer"], a["analysisName"], a["analysisId"], a["peril"], a["perilCode"], a["engineVersion"],
                               f"{a['currencyCode']} {a['currencyVintage']}".strip()]):
            ws.cell(r, 1 + j, v)
    items = [{"layer": a["layer"], "perilCode": a["perilCode"], "selected": True} for a in records]
    cov = matching.coverage(items)
    if len(cov):
        r += 2
        ws.cell(r, 1, "Layer x peril coverage (number of analyses)").font = _font(bold=True, color=TITLE_COLOR)
        r += 1
        for j, h in enumerate(cov.columns):
            c = ws.cell(r, 1 + j, h)
            c.font, c.fill = _font(bold=True, color="FFFFFF"), HEAD
        for _, row in cov.iterrows():
            r += 1
            for j, v in enumerate(row):
                ws.cell(r, 1 + j, None if (isinstance(v, float) and pd.isna(v)) else v)
    if warnings:
        r += 2
        ws.cell(r, 1, "Data and results checks").font = _font(bold=True, color=TITLE_COLOR)
        for lvl, msg in warnings:
            r += 1
            ws.cell(r, 1, {"error": "ERROR", "warn": "CHECK", "info": "NOTE"}.get(lvl, lvl)).alignment = Alignment(vertical="top")
            ws.cell(r, 2, msg).alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
            ws.row_dimensions[r].height = 15 * max(1, math.ceil(len(msg) / 120))


def _df_sheet(ws, df: pd.DataFrame, number_cols: dict | None = None, band=False, widths: dict | None = None):
    for j, c in enumerate(df.columns, 1):
        cell = ws.cell(1, j, c)
        cell.font, cell.fill = _font(bold=True, color="FFFFFF"), HEAD
    for i, row in enumerate(df.itertuples(index=False), 2):
        for j, v in enumerate(row, 1):
            ws.cell(i, j, None if (isinstance(v, float) and pd.isna(v)) else v)
            if band and i % 2 == 0:
                ws.cell(i, j).fill = BAND
    for name, fmt in (number_cols or {}).items():
        if name in df.columns:
            j = list(df.columns).index(name) + 1
            for i in range(2, len(df) + 2):
                ws.cell(i, j).number_format = fmt
    for j, c in enumerate(df.columns, 1):
        sample = [len(str(c))] + [len(str(x)) for x in df.iloc[:200, j - 1].tolist()]
        ws.column_dimensions[get_column_letter(j)].width = min(max(max(sample) + 4, 10), (widths or {}).get(c, 40))
    ws.freeze_panes = "A2"
    if len(df.columns):
        ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{max(len(df) + 1, 2)}"


def _rds_sheet(ws, df: pd.DataFrame):
    _df_sheet(ws, df, number_cols={c: "#,##0" for c in df.columns[2:]}, band=True)
    for row in ws.iter_rows(min_row=1, max_row=len(df) + 1, max_col=len(df.columns)):
        for c in row:
            c.border = Border(left=GREY, right=GREY, top=GREY, bottom=GREY)


def _meta_frame(records: list) -> pd.DataFrame:
    cols = ["Analysis ID", "Analysis Name", "Layer", "Peril", "Region", "Engine Version", "Loss Amplification", "Currency Code",
            "Currency Name", "Currency Vintage", "Model Profile", "Output Profile", "Username", "Exposure Name", "Description", "Analysis URL"]
    rows = [[a["analysisId"], a["analysisName"], a["layer"], a["peril"], a["region"], a["engineVersion"], a["lossAmplification"],
             a["currencyCode"], a["currencyName"], a["currencyVintage"], a["modelProfile"], a["outputProfile"], a["username"],
             a["exposureName"], a["description"], f"https://api-euw1.rms.com/platform/riskdata/v1/analyses/{a['analysisId']}"] for a in records]
    return pd.DataFrame(rows, columns=cols)


def _disclaimer_sheet(ws):
    ws.sheet_view.showGridLines = False
    try:
        img = XlImage(str(DATA_DIR / "moodys_rms_logo.jpeg"))
        img.width, img.height = 262, 139
        ws.add_image(img, "B2")
    except Exception:                                       # noqa: BLE001  (Pillow missing: the report still builds)
        pass
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 3, 120
    r = 12
    for para in [p.strip() for p in (DATA_DIR / "disclaimer.txt").read_text(encoding="utf-8").split("\n") if p.strip()]:
        c = ws.cell(r, 2, para)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = max(15, 15 * (len(para) // 115 + 1))
        r += 1


# ---------------------------------------------------------------- entry point
def build_workbook(*, submission: dict, locations: pd.DataFrame | None, recon: dict | None, records: list, layer_order: list,
                   rds: pd.DataFrame | None, warnings: list | None = None, yy_alias: set | None = None, review: dict | None = None,
                   timings: dict | None = None, event_perils: dict | None = None) -> bytes:
    """Return the .xlsx bytes. Everything needed is passed in, so this is testable without RMS or Streamlit."""
    wb = Workbook()
    ws = wb.active
    generated = datetime.now().strftime("%d/%m/%Y %H:%M")
    if review:
        ws.title = "Review"
        _review_sheet(ws, submission, review)
        acct = wb.create_sheet("Account")
    else:
        ws.title, acct = "Account", ws
    _account_sheet(acct, submission, recon, records, warnings or [], generated, timings or {}, layer_order)
    cols = ep_rules.results_columns(records, yy_alias)
    if locations is not None and len(locations):
        _exposure_summary(wb.create_sheet("Exposure Summary"), locations)
    if cols:
        _ep_sheet(wb.create_sheet("EP GrUp"), "GU", cols, None)
        layers = ep_rules.layers_in_order(records, layer_order)
        names = unique_sheet_names(layers, reserved={s.title for s in wb.worksheets})
        for layer in layers:
            _ep_sheet(wb.create_sheet(names[layer]), "GR", cols, layer)
    if rds is not None and len(rds):
        x = rds.copy()                                      # Peril / Status are appended, so the template's cells do not move
        if event_perils:
            ann = annotate(rds, event_perils, {r["perilCode"] for r in records if r["perilCode"] not in ("", "YY")})
            x["Peril"], x["Status"] = ann["Peril"], ann["Status"]
        _rds_sheet(wb.create_sheet("RDS Events"), x)
    if locations is not None and len(locations):
        _df_sheet(wb.create_sheet("Locations"), locations,
                  number_cols={"Location TIV": "#,##0", "Buildings TIV": "#,##0", "Contents TIV": "#,##0", "BI TIV": "#,##0"})
        _df_sheet(wb.create_sheet("Map"), loc_rules.map_aggregate(locations), number_cols={"Location TIV Sum": "#,##0", "LocID Count": "0"})
    if cols:
        res = ep_rules.results_frame(cols)
        wr = wb.create_sheet("Results")
        for i, row in enumerate(res.itertuples(index=False), 2):
            for j, v in enumerate(row, 1):
                wr.cell(i, j, None if (isinstance(v, float) and pd.isna(v)) else v)
                if 15 <= i <= 29 and j > 1:
                    wr.cell(i, j).number_format = NUM
        wr.column_dimensions["A"].width = 16
        for j in range(2, res.shape[1] + 1):
            wr.column_dimensions[get_column_letter(j)].width = 22
        wr.freeze_panes = "B4"
        _df_sheet(wb.create_sheet("Meta"), _meta_frame(records), widths={"Analysis URL": 60})
    _disclaimer_sheet(wb.create_sheet("RMS Disclaimer"))
    for sheet in wb.worksheets:                             # print-ready: landscape, one page wide
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
