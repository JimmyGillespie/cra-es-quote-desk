"""CRA report workbook (.xlsx, macro-free) - rebuilds the tabs of CRA_E&S_Template.xlsm.

Tabs: Account (replaces Controls) | Exposure Summary | EP GrUp | <one tab per layer> | RDS Events | Locations | Map |
      Results | Meta | RMS Disclaimer.
EP tabs keep the template's cell positions (AEP rows 8-19, statistics rows 21-24, OEP rows 31-42) so any link or
copy-paste habit built on the template still lands on the right cell.

Values are written as numbers, not INDEX/MATCH formulas: the workbook is e-mailed and previewed outside Excel, where
uncalculated formulas show as blank. The 'Results' tab holds the full source data for audit.
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.drawing.image import Image as XlImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..constants import (DATA_DIR, HIGHLIGHT_RP, PERIL_COLUMNS, RP_LIST, TIV_DELTA_ALERT, TIV_DELTA_WARN)
from ..rules import ep as ep_rules
from ..rules import locations as loc_rules
from ..rules.layers import unique_sheet_names

BODY = "Aptos Narrow"
TITLE_COLOR = "156082"
HILITE = PatternFill("solid", fgColor="C1E4F5")
HEAD = PatternFill("solid", fgColor="0066CC")
BAND = PatternFill("solid", fgColor="F2F2F2")
THIN, MED = Side(style="thin"), Side(style="medium")
GREY = Side(style="thin", color="C8C8C8")
NUM = '_-* #,##0_-;\\-* #,##0_-;_-* "-"??_-;_-@_-'
CV_FMT = '0.00'


def _font(**kw):
    return Font(name=kw.pop("name", BODY), size=kw.pop("size", 11), **kw)


def _box(ws, r1, c1, r2, c2):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            cell = ws.cell(r, c)
            cell.border = Border(left=MED if c == c1 else THIN, right=MED if c == c2 else THIN,
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
            cell = ws.cell(r, 4 + j, _nz(tbl.iloc[i][label]))
            cell.number_format = NUM
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


def _chart(ws, title: str, first_row: int, cols: list, perspective: str, layer: str | None):
    """AEP line chart: one series per peril that has data (the template charted the single perils)."""
    from openpyxl.chart import Series
    tbl = ep_rules.ep_table(cols, perspective, "AEP", layer)
    ch = LineChart()
    ch.title, ch.height, ch.width = title, 10.5, 19
    ch.y_axis.title, ch.x_axis.title = "Loss", "Return period (years)"
    ch.y_axis.number_format = "#,##0"
    ch.y_axis.delete = False
    ch.x_axis.delete = False
    last = first_row + len(RP_LIST) - 1
    for j, (_code, label) in enumerate(PERIL_COLUMNS):
        if any(v for v in tbl[label] if v):
            s = Series(Reference(ws, min_col=4 + j, min_row=first_row, max_row=last), title=label)
            s.smooth = False
            ch.series.append(s)
    if ch.series:
        ch.set_categories(Reference(ws, min_col=3, min_row=first_row, max_row=last))
        ws.add_chart(ch, "N4")


def _ep_sheet(ws, perspective: str, cols: list, layer: str | None):
    ws.sheet_view.showGridLines = False
    is_gu = perspective == "GU"
    ws["B1"] = "Perspective"
    ws["B1"].font = _font(name="Arial", bold=True, color=TITLE_COLOR)
    ws["C1"] = "Ground Up Loss Overview" if is_gu else "Gross Loss Overview"
    ws["C1"].font = _font(name="Arial", bold=True, color=TITLE_COLOR)
    if not is_gu:
        ws["B2"] = "Layer"
        ws["B2"].font = _font(name="Arial", bold=True, color=TITLE_COLOR)
        ws["C2"] = layer
    ws["B3"] = perspective
    _write_ep_block(ws, 4, "AEP", perspective, cols, layer, 8)
    _write_stats(ws, 21, perspective, cols, layer)
    _write_ep_block(ws, 27, "OEP", perspective, cols, layer, 31)
    for col, w in {"A": 3, "B": 12.4, "C": 13.4, "D": 15, "E": 15, "F": 15, "G": 15, "H": 15, "I": 15, "J": 15, "K": 15}.items():
        ws.column_dimensions[col].width = w
    for r in (6, 7, 29, 30):                                  # the template kept its lookup keys on hidden rows
        ws.row_dimensions[r].hidden = True
    _chart(ws, ("AEP Ground Up Loss" if is_gu else f"AEP Gross Loss - {layer}"), 8, cols, perspective, layer)


# ---------------------------------------------------------------- summary tables
def _summary_table(ws, row: int, col: int, title: str, df: pd.DataFrame) -> int:
    """Pivot-style table. Returns the row after it."""
    nested = list(df.columns[:2]) == ["Admin1", "Admin2"]
    heads = (["State / County"] if nested else [str(df.columns[0])]) + ["Loc Count", "Sum of Location TIV"]
    for j, h in enumerate(heads):
        c = ws.cell(row, col + j, h)
        c.font = _font(bold=True)
        c.alignment = Alignment(horizontal="left" if j == 0 else "right")
    r = row + 1
    if nested:
        for state, g in df.groupby("Admin1", sort=True):
            ws.cell(r, col, state).font = _font(bold=True)
            ws.cell(r, col + 1, int(g["Loc Count"].sum())).font = _font(bold=True)
            c = ws.cell(r, col + 2, float(g["Sum of Location TIV"].sum()))
            c.font, c.number_format = _font(bold=True), '#,##0'
            r += 1
            for _, x in g.sort_values("Admin2").iterrows():
                ws.cell(r, col, "   " + str(x["Admin2"]))
                ws.cell(r, col + 1, int(x["Loc Count"]))
                ws.cell(r, col + 2, float(x["Sum of Location TIV"])).number_format = '#,##0'
                r += 1
    else:
        for _, x in df.iterrows():
            ws.cell(r, col, str(x.iloc[0]))
            ws.cell(r, col + 1, int(x["Loc Count"]))
            ws.cell(r, col + 2, float(x["Sum of Location TIV"])).number_format = '#,##0'
            r += 1
    ws.cell(r, col, "Total Result").font = _font(bold=True)
    ws.cell(r, col + 1, int(df["Loc Count"].sum())).font = _font(bold=True)
    c = ws.cell(r, col + 2, float(df["Sum of Location TIV"].sum()))
    c.font, c.number_format = _font(bold=True), '#,##0'
    _box(ws, row, col, r, col + 2)
    for k in range(3):
        ws.cell(r, col + k).border = Border(left=ws.cell(r, col + k).border.left, right=ws.cell(r, col + k).border.right,
                                            top=MED, bottom=MED)
    ws.column_dimensions[get_column_letter(col)].width = 26 if nested else 22
    ws.column_dimensions[get_column_letter(col + 1)].width = 13
    ws.column_dimensions[get_column_letter(col + 2)].width = 23
    return r + 1


def _exposure_summary(ws, df: pd.DataFrame):
    ws.sheet_view.showGridLines = False
    s = loc_rules.exposure_summaries(df)
    end = _summary_table(ws, 2, 2, "Account", s["Account"])
    _summary_table(ws, max(end + 2, 11), 2, "Wind Tier", s["Wind Tier"])
    col = 6
    for key in ("State / County", "Construction", "Occupancy", "Geocoding Resolution", "Year Built Band", "Stories Band"):
        _summary_table(ws, 2, col, key, s[key])
        col += 4


# ---------------------------------------------------------------- other tabs
def _account_sheet(ws, sub: dict, recon: dict | None, records: list, warnings: list, generated: str):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 58
    for c, w in zip("CDEFGH", (14, 26, 12, 12, 14, 14)):
        ws.column_dimensions[c].width = w
    ws["A1"] = "CRA E&S - Account"
    ws["A1"].font = _font(name="Arial", bold=True, size=14, color=TITLE_COLOR)
    rows = [("EDM / Exposure", sub.get("exposure_name")), ("Exposure ID", sub.get("exposure_id")),
            ("Account", sub.get("account_name")), ("Underwriter", sub.get("underwriter")),
            ("Priority", sub.get("priority")), ("Quote terms", sub.get("comments")), ("Generated", generated)]
    r = 3
    for k, v in rows:
        ws.cell(r, 1, k).font = _font(bold=True)
        ws.cell(r, 2, v)
        ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    r += 1
    ws.cell(r, 1, "TIV check").font = _font(bold=True, color=TITLE_COLOR)
    r += 1
    if recon:
        for k, v, f in [("TIV Reported", recon.get("tiv_reported"), '#,##0'), ("TIV Modelled", recon.get("tiv_modelled"), '#,##0'),
                        ("Delta", recon.get("delta"), '0.0%'), ("Locations", recon.get("locations"), '#,##0')]:
            ws.cell(r, 1, k)
            c = ws.cell(r, 2, v)
            c.number_format, c.alignment = f, Alignment(horizontal="left")
            if k == "Delta" and v is not None:
                if abs(v) >= TIV_DELTA_ALERT:
                    c.fill = PatternFill("solid", fgColor="F8CBAD")
                elif abs(v) >= TIV_DELTA_WARN:
                    c.fill = PatternFill("solid", fgColor="FFE699")
            r += 1
    r += 1
    ws.cell(r, 1, "Analyses used").font = _font(bold=True, color=TITLE_COLOR)
    r += 1
    heads = ["Layer", "Analysis name", "Analysis ID", "Peril", "Peril code", "Engine", "Currency"]
    for j, h in enumerate(heads):
        c = ws.cell(r, 1 + j, h)
        c.font, c.fill = _font(bold=True, color="FFFFFF"), HEAD
    for a in records:
        r += 1
        for j, v in enumerate([a["layer"], a["analysisName"], a["analysisId"], a["peril"], a["perilCode"],
                               a["engineVersion"], f"{a['currencyCode']} {a['currencyVintage']}".strip()]):
            ws.cell(r, 1 + j, v)
    if warnings:
        r += 2
        ws.cell(r, 1, "Data and results checks").font = _font(bold=True, color=TITLE_COLOR)
        for lvl, msg in warnings:
            r += 1
            ws.cell(r, 1, {"error": "ERROR", "warn": "CHECK", "info": "NOTE"}.get(lvl, lvl)).alignment = Alignment(vertical="top")
            ws.cell(r, 2, msg).alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
            ws.row_dimensions[r].height = 30 if len(msg) > 90 else 15


def _df_sheet(ws, df: pd.DataFrame, number_cols: dict | None = None, header_fill=True, band=False, widths: dict | None = None):
    for j, c in enumerate(df.columns, 1):
        cell = ws.cell(1, j, c)
        cell.font = _font(bold=True, color="FFFFFF" if header_fill else "000000")
        if header_fill:
            cell.fill = HEAD
    for i, row in enumerate(df.itertuples(index=False), 2):
        for j, v in enumerate(row, 1):
            if isinstance(v, float) and pd.isna(v):
                v = None
            ws.cell(i, j, v)
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
    cols = ["Analysis ID", "Analysis Name", "Layer", "Peril", "Region", "Engine Version", "Loss Amplification",
            "Currency Code", "Currency Name", "Currency Vintage", "Model Profile", "Output Profile", "Username",
            "Exposure Name", "Description", "Analysis URL"]
    rows = [[a["analysisId"], a["analysisName"], a["layer"], a["peril"], a["region"], a["engineVersion"],
             a["lossAmplification"], a["currencyCode"], a["currencyName"], a["currencyVintage"], a["modelProfile"],
             a["outputProfile"], a["username"], a["exposureName"], a["description"],
             f"https://api-euw1.rms.com/platform/riskdata/v1/analyses/{a['analysisId']}"] for a in records]
    return pd.DataFrame(rows, columns=cols)


def _disclaimer_sheet(ws):
    ws.sheet_view.showGridLines = False
    try:
        img = XlImage(str(DATA_DIR / "moodys_rms_logo.jpeg"))
        img.width, img.height = 262, 139
        ws.add_image(img, "B2")
    except Exception:                                       # noqa: BLE001  (Pillow missing: report still builds)
        pass
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 120
    text = (DATA_DIR / "disclaimer.txt").read_text(encoding="utf-8")
    r = 12
    for para in [p.strip() for p in text.split("\n") if p.strip()]:
        c = ws.cell(r, 2, para)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = max(15, 15 * (len(para) // 115 + 1))
        r += 1


# ---------------------------------------------------------------- entry point
def build_workbook(*, submission: dict, locations: pd.DataFrame | None, recon: dict | None, records: list,
                   layer_order: list, rds: pd.DataFrame | None, warnings: list | None = None,
                   yy_alias: set | None = None) -> bytes:
    """Return the .xlsx bytes. Everything needed is passed in, so this is testable without RMS or Streamlit."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Account"
    generated = datetime.now().strftime("%d/%m/%Y %H:%M")
    _account_sheet(ws, submission, recon, records, warnings or [], generated)
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
        _rds_sheet(wb.create_sheet("RDS Events"), rds)
    if locations is not None and len(locations):
        _df_sheet(wb.create_sheet("Locations"), locations,
                  number_cols={"Location TIV": "#,##0", "Buildings TIV": "#,##0", "Contents TIV": "#,##0", "BI TIV": "#,##0"})
        mp = loc_rules.map_aggregate(locations)
        _df_sheet(wb.create_sheet("Map"), mp, number_cols={"Location TIV Sum": "#,##0", "LocID Count": "0"})
    if cols:
        res = ep_rules.results_frame(cols)
        wr = wb.create_sheet("Results")
        for i, row in enumerate(res.itertuples(index=False), 2):
            for j, v in enumerate(row, 1):
                if isinstance(v, float) and pd.isna(v):
                    v = None
                wr.cell(i, j, v)
                if 15 <= i <= 29 and j > 1:
                    wr.cell(i, j).number_format = NUM
        wr.column_dimensions["A"].width = 16
        for j in range(2, res.shape[1] + 1):
            wr.column_dimensions[get_column_letter(j)].width = 22
        wr.freeze_panes = "B4"
        _df_sheet(wb.create_sheet("Meta"), _meta_frame(records), widths={"Analysis URL": 60})
    _disclaimer_sheet(wb.create_sheet("RMS Disclaimer"))
    from openpyxl.worksheet.properties import PageSetupProperties
    for sheet in wb.worksheets:                              # print-ready: landscape, one page wide
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
