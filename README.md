# CRA E&S Quote Desk

Streamlit rebuild of `CRA_E&S_Template.xlsm`:  **Account -> Exposure -> Hazards -> Analyses -> Results -> Report**.
Every quote is saved and can be re-opened. The AI Summary tab is not included.

```
app.py                  entry point (st.segmented_control step bar, no sidebar)
views/                  one file per step (account, exposure, hazards, analyses, results, report)
cra/
  config.py             settings from environment / secrets
  constants.py          return periods, perils, columns (each tagged with the VBA it came from)
  rms/core.py           keys, HTTP, tolerant JSON getters (ported from the EQ-RMS app)
  rms/service.py        every RMS call the workbook made
  rms/demo.py           offline synthetic RMS (CRA_DEMO=1)
  rules/                the business rules: locations, wind tier, hazards, EP, RDS, matching, e-mail, naming
  pipeline.py           orchestration; each step saves before it returns
  store.py              account store (atomic, verified, mirrored)
  graph.py              Microsoft Graph: find the submission e-mail, create a reply draft
  report/excel.py       the report workbook (.xlsx)
tests/                  38 tests, incl. parity with the template's cached values
docs/MIGRATION_SPEC.md  traceability, rules, deliberate differences, open items
```

## Run

```
pip install -r requirements.txt
CRA_DEMO=1 streamlit run app.py        # synthetic data, no keys, nothing touches RMS
streamlit run app.py                   # live
python -m pytest tests -q
```

## Environment variables (Posit Connect: Vars tab)

| Variable | Use |
|---|---|
| `RISKDATA_API_KEY` | Risk Data + Risk Modeler (`RMS_TOKEN` also accepted) |
| `LI_API_KEY` | Location Intelligence (hazards) |
| `GROUPING_API_KEY` | Grouping API (falls back to the Risk Data key, shown in the connection panel) |
| `GROUPING_RESOURCE_GROUP_ID` | Grouping resource group, if the key needs one |
| `RMS_HOST` | default `api-euw1.rms.com` |
| `CRA_SHARE_DIR` | primary store, default `/mnt/from-posit/content/anv-cra-es` |
| `CRA_STORE_DIR` | local fallback store, default `~/.cra_es_store` |
| `CRA_MAILBOX` | default `exposuremanagement@amtrustgroup.com` |
| `CRA_QUOTES_ROOT` | where "Copy to quotes folder" writes |
| `MS_TENANT_ID`, `MS_CLIENT_ID` or `MSGRAPH_TOKEN` | Microsoft sign-in for the e-mail features |

## Deploy to Posit Connect

`rsconnect write-manifest streamlit --entrypoint app.py .` then push the folder (with `manifest.json`) to GitHub and publish from Git, as for the EQ-RMS app.
Add `posit-sdk` to `requirements.txt` if the e-mail features should use the viewer's own Microsoft token.

## Before first live use

1. Confirm the model-profile IDs on the Analyses page (see spec section 5). 2. Press **Test connections** in the header panel. 3. Compare three real accounts with the workbook (spec section 7). 4. Rotate the API keys typed into the VBA.
