# CRA E&S Quote Desk

Streamlit rebuild of `CRA_E&S_Template.xlsm`:  **Account -> Exposure -> Hazards -> Analyses -> Results -> Report**.
Pick an EDM and the app finds its results in RMS; every quote is saved and can be re-opened. No AI.

```
app.py                  entry point (step bar, no sidebar)
views/                  one file per step + common helpers
cra/charts.py           all charts (Altair: log return-period axis, $M labels, 250-year marker)
cra/pipeline.py         orchestration; each step saves before it returns
cra/rules/              business rules: locations, hazards, EP, RDS, matching, layers, review, e-mail
cra/rms/                RMS client (core, service) and an offline demo
cra/report/excel.py     the workbook (Review tab, native Excel charts)
cra/store.py            account store (atomic, verified, mirrored)
tests/                  47+ tests; the golden set is a real account (tests/fixtures is git-ignored)
docs/                   MIGRATION_SPEC.md, EXCEL_COMPARISON.md
```

Run:  `pip install -r requirements.txt`;  `CRA_DEMO=1 streamlit run app.py` (synthetic data, no keys);  `python -m pytest tests -q`.

Variables (Posit Connect Vars tab): `RISKDATA_API_KEY`, `LI_API_KEY`, `GROUPING_API_KEY`, `GROUPING_RESOURCE_GROUP_ID`, `RMS_HOST`,
`CRA_SHARE_DIR` (default `/mnt/from-posit/content/anv-cra-es`), `CRA_STORE_DIR`, `CRA_MAILBOX`, `CRA_QUOTES_ROOT`, `MS_TENANT_ID` / `MS_CLIENT_ID`.

Deploy: `py prepare_deploy.py` writes `manifest.json` (Python 3.11.9 and the `pkg-resources-fix` image, as `anv-eq-rms-modeller`), then
upload the changed files to GitHub and press Update Now in Posit Connect.
