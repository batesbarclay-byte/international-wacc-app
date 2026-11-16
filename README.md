# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD** and in the **currency of any selected country**.

## Features
- **Live data, with sources and dates shown and a Refresh button** (cached 24 hours)
  - Unlevered industry betas: [Damodaran, betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls)
  - BBB default spread (large non-financial service firms): [Damodaran, ratings.htm](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.htm)
  - Corporate tax rates (combined statutory CIT): OECD Corporate Tax Statistics
  - 10-year government bond yields: [Trading Economics](https://tradingeconomics.com/bonds)
  - If a source can't be reached, stored fallback values are used and the app flags them.
- **Inputs:** industry, target country, equity risk premium, target debt share, and a toggle for the project tax rate (U.S. or selected country).
- **Manual overrides:** unlevered beta, both corporate tax rates, USD BBB spread, both 10-year yields, and the foreign-currency BBB spread (when off, the auto-calculated spread is shown).
- **Outputs:** side-by-side USD and foreign-currency results, an assumptions panel, an all-industry comparison and CSV downloads.

**Defaults when the app opens:** Software (System & Application), United States, U.S. tax rate, 6.5% ERP and 30% debt.

## Formulas
| Item | Formula |
|---|---|
| Levered beta | β_L = β_U × [1 + (1 − t) × D/E], D/E = w_D/(1 − w_D) |
| USD cost of equity | r_E,USD = y_US + β_L × ERP |
| USD cost of debt | r_D,USD = y_US + BBB spread |
| Foreign-currency rate | r_FC = (1 + r_USD) × (1 + y_FC)/(1 + y_US) − 1 |
| FC cost of debt | Auto: converted r_D,USD. Override: y_FC + manual spread |
| WACC | (1 − w_D) × r_E + w_D × r_D × (1 − t) |

The same project tax rate, debt share and ERP are used in both currencies.

## Built-in checks
- Equal government bond yields give equal USD and FC results, with the FC spread override off.
- Zero debt makes WACC equal the cost of equity.
- Overriding the FC spread leaves USD results unchanged.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
Push `app.py`, `requirements.txt` and `README.md` to a public GitHub repository. Then, at [share.streamlit.io](https://share.streamlit.io), choose **Create app**, select the repository, set the main file to `app.py`, and deploy.

*Built with AI assistance (Claude); calculations verified independently.*
