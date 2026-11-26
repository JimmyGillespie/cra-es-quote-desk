"""Step 6 - build the workbook, keep a copy with the quote, and prepare the reply (SaveFile / ReplyFoundEmail)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from cra import charts, graph
from cra.rules.naming import folder_name_from_exposure, now_stamp, report_file_name

from .common import ctx, finding_boxes, flags, kpis, money, need_account, section
from .exposure import _token


def render():
    p = need_account()
    if not p:
        return
    c = ctx()
    sub = p.submission()
    inp = p.report_inputs()
    if not inp["records"]:
        st.info("Pull results first (Results step).", icon=":material/arrow_back:")
        return
    section("Build the workbook")
    rec, rev = inp["recon"] or {}, inp["review"]
    kpis([("Layers", str(len({a["layer"] for a in inp["records"]})), None), ("Analyses", str(len(inp["records"])), None),
          ("Locations", f"{rec.get('locations', 0):,}" if rec else "not loaded", None),
          ("TIV modelled", charts.fmt_money(rec.get("tiv_modelled")) if rec else "-", None),
          ("Review", f"{rev['counts']['red']} red · {rev['counts']['amber']} amber", "first tab of the workbook")])
    st.write("")
    if not rec:
        st.info("Locations are not loaded, so the workbook will have no Exposure Summary, Locations or Map tabs and the review has no data checks. "
                "Load them on the Exposure step if you want them.", icon=":material/info:")
    reds = [f for f in rev["findings"] if f["level"] == "red"]
    if reds:
        finding_boxes(reds, ("red",))
    flags(inp["warnings"])
    go = True
    if reds or any(l == "error" for l, _m in inp["warnings"]):
        go = st.checkbox("I have reviewed the red flags / errors above and want to build the report anyway")
    stamp_name = report_file_name(sub["exposure_name"], now_stamp())
    if st.button("Build report", type="primary", disabled=not go):
        with st.spinner("Building the workbook..."):
            data = p.build_report()
        rep = p.store.save_file(p.id, f"reports/{stamp_name}", data)
        p.store.save_file(p.id, f"runs/{stamp_name.rsplit('.', 1)[0]}/{stamp_name}", data)
        st.session_state["last_report"] = (p.id, stamp_name, data)
        (st.success if rep.ok else st.error)(f"{stamp_name} - {rep.text()}")

    last = st.session_state.get("last_report")
    files = p.store.list_files(p.id, "reports")
    if last and last[0] == p.id:
        st.download_button("Download workbook", last[2], file_name=last[1], type="primary",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    loc = inp["locations"]
    if loc is not None:
        st.download_button("Download locations with hazards (CSV)", loc.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{report_file_name(sub['exposure_name'], ext='csv').replace('.csv', '')}_Locations.csv", mime="text/csv")
    if files:
        with st.expander(f"Saved reports ({len(files)})"):
            for rel, _t in files:
                data = p.store.read_file(p.id, rel)
                if data:
                    st.download_button(rel.split("/")[-1], data, file_name=rel.split("/")[-1], key=f"dl_{rel}",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    section("Quotes folder", "The macro saved a copy under the Quotes\\2026 folder on the NAS, in a folder named after the account.")
    folder = Path(c.settings.quotes_root) / folder_name_from_exposure(sub["exposure_name"])
    st.code(str(folder), language=None)
    root_ok = Path(c.settings.quotes_root).exists()
    cur = last[2] if (last and last[0] == p.id) else (p.store.read_file(p.id, files[0][0]) if files else None)
    name = last[1] if (last and last[0] == p.id) else (files[0][0].split("/")[-1] if files else "")
    if not root_ok:
        st.caption("That path is not reachable from this server (a Windows NAS path is not visible from Linux). "
                   "Download the workbook and save it there, or set CRA_QUOTES_ROOT to a path this server can write to.")
    if st.button("Copy to quotes folder", disabled=not (root_ok and cur)):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(cur)
            st.success(f"Saved {folder / name}")
        except OSError as exc:
            st.error(f"Could not save there: {exc}")

    section("Reply to the underwriter", "Creates a draft for you to review and send - nothing is sent automatically.")
    if not cur:
        st.caption("Build the report first.")
        return
    atts = [(name, cur, graph.XLSX_MIME)]
    subject = ("RE: " + sub["email_subject"]) if sub.get("email_subject") else f"Results for {sub['exposure_name']}"
    tok, _err = _token()
    if sub.get("email_id") and tok:
        if st.button("Create reply draft in the shared mailbox"):
            try:
                out = graph.create_reply_draft(tok, c.settings.mailbox, {"id": sub["email_id"], "to": sub.get("email_to", [])},
                                               sub["exposure_name"], sub.get("underwriter", ""), atts)
                st.success("Draft created in " + c.settings.mailbox + ".")
                if out.get("webLink"):
                    st.link_button("Open the draft", out["webLink"])
            except graph.GraphError as exc:
                st.error(str(exc))
    else:
        st.caption("Link the submission e-mail on the Exposure step and sign in to Microsoft to create the draft here. Otherwise download an e-mail file and open it in Outlook.")
    eml = graph.build_eml(sub.get("email_to", []), subject, graph.reply_body(sub["exposure_name"], sub.get("underwriter", "")), atts)
    st.download_button("Download e-mail draft (.eml)", eml, file_name="CRA_results_draft.eml", mime="message/rfc822")
