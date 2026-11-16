"""
International Cost of Capital App
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, cached 24h, with a refresh button):
  - Unlevered industry betas: Aswath Damodaran, betas.xls (U.S. industries)
  - BBB default spread: Damodaran, ratings.htm (large non-financial service firms)
  - Corporate tax rates: OECD Corporate Tax Statistics (combined statutory CIT rate)
  - 10-year government bond yields: Trading Economics (tradingeconomics.com/bonds)
If a source cannot be reached, the app falls back to stored values and says so.
"""

import io
import re
from datetime import date, datetime

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.htm"
OECD_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
TE_URL = "https://tradingeconomics.com/bonds"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # %
DEFAULT_DEBT = 30.0  # % of capital

# Country -> (ISO3 code used by OECD, currency code)
COUNTRIES = {
    "United States": ("USA", "USD"), "Canada": ("CAN", "CAD"), "Mexico": ("MEX", "MXN"),
    "Brazil": ("BRA", "BRL"), "Chile": ("CHL", "CLP"), "Colombia": ("COL", "COP"),
    "United Kingdom": ("GBR", "GBP"), "Germany": ("DEU", "EUR"), "France": ("FRA", "EUR"),
    "Italy": ("ITA", "EUR"), "Spain": ("ESP", "EUR"), "Netherlands": ("NLD", "EUR"),
    "Belgium": ("BEL", "EUR"), "Austria": ("AUT", "EUR"), "Portugal": ("PRT", "EUR"),
    "Ireland": ("IRL", "EUR"), "Switzerland": ("CHE", "CHF"), "Sweden": ("SWE", "SEK"),
    "Norway": ("NOR", "NOK"), "Denmark": ("DNK", "DKK"), "Poland": ("POL", "PLN"),
    "Czech Republic": ("CZE", "CZK"), "Hungary": ("HUN", "HUF"), "Turkey": ("TUR", "TRY"),
    "Israel": ("ISR", "ILS"), "Japan": ("JPN", "JPY"), "South Korea": ("KOR", "KRW"),
    "China": ("CHN", "CNY"), "India": ("IND", "INR"), "Indonesia": ("IDN", "IDR"),
    "Australia": ("AUS", "AUD"), "New Zealand": ("NZL", "NZD"), "South Africa": ("ZAF", "ZAR"),
}

# Trading Economics names that differ from ours
TE_ALIASES = {
    "US": "United States", "U.S.": "United States", "UK": "United Kingdom",
    "Britain": "United Kingdom", "Korea": "South Korea", "Czechia": "Czech Republic",
    "Turkiye": "Turkey", "Türkiye": "Turkey",
}

# Offline fallbacks (used ONLY if a live fetch fails; approximate, refresh to replace)
FALLBACK_BETAS = {
    "Software (System & Application)": 1.20, "Software (Internet)": 1.30,
    "Semiconductor": 1.45, "Computers/Peripherals": 1.10, "Drugs (Pharmaceutical)": 0.95,
    "Healthcare Products": 0.95, "Retail (General)": 0.80, "Auto & Truck": 1.05,
    "Utility (General)": 0.35, "Oil/Gas (Integrated)": 0.80, "Telecom. Services": 0.55,
    "Food Processing": 0.55, "Total Market": 0.90,
}
FALLBACK_BBB_SPREAD = 1.11  # %
FALLBACK_TAX = {  # combined statutory CIT, %
    "United States": 25.6, "Canada": 26.1, "Mexico": 30.0, "Brazil": 34.0, "Chile": 27.0,
    "Colombia": 35.0, "United Kingdom": 25.0, "Germany": 29.9, "France": 25.8,
    "Italy": 27.8, "Spain": 25.0, "Netherlands": 25.8, "Belgium": 25.0, "Austria": 23.0,
    "Portugal": 31.5, "Ireland": 12.5, "Switzerland": 19.6, "Sweden": 20.6, "Norway": 22.0,
    "Denmark": 22.0, "Poland": 19.0, "Czech Republic": 21.0, "Hungary": 9.0, "Turkey": 25.0,
    "Israel": 23.0, "Japan": 29.7, "South Korea": 26.4, "China": 25.0, "India": 25.2,
    "Indonesia": 22.0, "Australia": 30.0, "New Zealand": 28.0, "South Africa": 27.0,
}
FALLBACK_YIELDS = {  # 10-year, %
    "United States": 4.30, "Canada": 3.30, "Mexico": 9.00, "Brazil": 13.50, "Chile": 5.50,
    "Colombia": 11.50, "United Kingdom": 4.60, "Germany": 2.70, "France": 3.40,
    "Italy": 3.60, "Spain": 3.20, "Netherlands": 2.90, "Belgium": 3.30, "Austria": 3.00,
    "Portugal": 3.10, "Ireland": 3.00, "Switzerland": 0.30, "Sweden": 2.60, "Norway": 4.00,
    "Denmark": 2.50, "Poland": 5.50, "Czech Republic": 4.30, "Hungary": 6.80,
    "Turkey": 30.00, "Israel": 4.20, "Japan": 1.70, "South Korea": 3.00, "China": 1.80,
    "India": 6.40, "Indonesia": 6.80, "Australia": 4.40, "New Zealand": 4.60,
    "South Africa": 9.50,
}


# ----------------------------------------------------------------------------
# Core calculations (pure functions; all rates as decimals)
# ----------------------------------------------------------------------------
def levered_beta(beta_u: float, tax: float, debt_share: float) -> float:
    """Hamada: beta_L = beta_U * [1 + (1 - t) * D/E], with D/E = w_D / (1 - w_D)."""
    if debt_share >= 1:
        raise ValueError("Debt share must be below 100%.")
    de = debt_share / (1 - debt_share)
    return beta_u * (1 + (1 - tax) * de)


def compute_cost_of_capital(
    beta_u: float,
    tax: float,
    debt_share: float,
    erp: float,
    rf_usd: float,
    rf_fc: float,
    spread_usd: float,
    spread_fc_override: float | None = None,
) -> dict:
    """
    USD:
      cost of equity  r_E$ = r_f$ + beta_L * ERP
      cost of debt    r_D$ = r_f$ + BBB spread (USD)
      WACC$           = (1 - w_D) r_E$ + w_D r_D$ (1 - t)
    Foreign currency (FC), via the ratio of 10-year government bond yields
    (interest rate parity / relative Fisher):
      r_FC = (1 + r_$) * (1 + y_FC) / (1 + y_$) - 1
      FC cost of debt: auto = converted USD cost of debt  (implied FC spread = r_D,FC - y_FC)
                       override = y_FC + manual FC spread
      WACC_FC         = (1 - w_D) r_E,FC + w_D r_D,FC (1 - t)
    """
    b_l = levered_beta(beta_u, tax, debt_share)
    w_e = 1 - debt_share

    re_usd = rf_usd + b_l * erp
    rd_usd = rf_usd + spread_usd
    wacc_usd = w_e * re_usd + debt_share * rd_usd * (1 - tax)

    k = (1 + rf_fc) / (1 + rf_usd)
    re_fc = (1 + re_usd) * k - 1
    rd_fc_auto = (1 + rd_usd) * k - 1
    if spread_fc_override is None:
        rd_fc = rd_fc_auto
    else:
        rd_fc = rf_fc + spread_fc_override
    spread_fc = rd_fc - rf_fc
    wacc_fc = w_e * re_fc + debt_share * rd_fc * (1 - tax)

    return {
        "levered_beta": b_l,
        "re_usd": re_usd, "rd_usd": rd_usd,
        "rd_usd_after_tax": rd_usd * (1 - tax), "wacc_usd": wacc_usd,
        "re_fc": re_fc, "rd_fc": rd_fc, "rd_fc_after_tax": rd_fc * (1 - tax),
        "wacc_fc": wacc_fc, "spread_fc": spread_fc, "spread_fc_auto": rd_fc_auto - rf_fc,
        "fx_factor": k,
    }


# ----------------------------------------------------------------------------
# Data fetchers (each returns (data, source_note, as_of, live_flag))
# ----------------------------------------------------------------------------
def _get(url: str, timeout: int = 20) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_betas():
    try:
        raw = _get(BETAS_URL).content
        df = pd.read_excel(io.BytesIO(raw), sheet_name="Industry Averages", header=None)
        hdr = df.index[df.apply(lambda r: r.astype(str).str.contains("Industry Name").any(), axis=1)][0]
        as_of = None
        for _, row in df.iloc[:hdr].iterrows():
            vals = [v for v in row.tolist() if pd.notna(v)]
            for i, v in enumerate(vals):
                if "date updated" in str(v).lower() and i + 1 < len(vals):
                    as_of = vals[i + 1]
        if isinstance(as_of, (datetime, pd.Timestamp)):
            as_of = as_of.strftime("%Y-%m-%d")
        table = df.iloc[hdr + 1:].copy()
        table.columns = [str(c).strip() for c in df.iloc[hdr]]
        ub_col = next(c for c in table.columns
                      if c.lower().startswith("unlevered beta") and "cash" not in c.lower())
        out = table[["Industry Name", ub_col]].dropna()
        out = out[out["Industry Name"].astype(str).str.strip() != ""]
        betas = {str(n).strip(): float(b) for n, b in zip(out["Industry Name"], out[ub_col])}
        if not betas:
            raise ValueError("empty beta table")
        return betas, "Damodaran, betas.xls (Industry Averages, unlevered beta)", str(as_of or date.today()), True
    except Exception as e:  # noqa: BLE001
        return FALLBACK_BETAS, f"Fallback values (Damodaran fetch failed: {type(e).__name__})", "stored", False


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_bbb_spread():
    try:
        html = _get(RATINGS_URL).text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;|\s+", " ", text)
        # First BBB row on the page = large non-financial service firms table
        m = re.search(r"Baa2\s*/\s*BBB\s+([0-9]+\.[0-9]+)\s*%", text)
        if not m:
            raise ValueError("BBB row not found")
        spread = float(m.group(1))
        d = re.search(r"(January|February|March|April|May|June|July|August|September|October|"
                      r"November|December)\s+20\d\d", text)
        as_of = d.group(0) if d else str(date.today())
        return spread, "Damodaran, ratings.htm (Baa2/BBB, large non-financial service firms)", as_of, True
    except Exception as e:  # noqa: BLE001
        return FALLBACK_BBB_SPREAD, f"Fallback value (Damodaran fetch failed: {type(e).__name__})", "stored", False


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_tax_rates():
    try:
        df = pd.read_csv(io.StringIO(_get(OECD_URL, timeout=40).text))
        # Keep the combined (central + sub-central) statutory rate series
        label_cols = [c for c in df.columns if df[c].dtype == object]
        mask = pd.Series(False, index=df.index)
        for c in label_cols:
            mask |= df[c].astype(str).str.contains("Combined corporate income tax rate", case=False)
        sub = df[mask] if mask.any() else df
        sub = sub[["REF_AREA", "TIME_PERIOD", "OBS_VALUE"]].dropna()
        sub = sub.sort_values("TIME_PERIOD").groupby("REF_AREA").tail(1)
        by_iso = dict(zip(sub["REF_AREA"], sub["OBS_VALUE"].astype(float)))
        year = str(sub["TIME_PERIOD"].max())
        rates = {}
        for name, (iso, _) in COUNTRIES.items():
            rates[name] = by_iso.get(iso, FALLBACK_TAX.get(name))
        if by_iso.get("USA") is None:
            raise ValueError("US rate missing")
        return rates, "OECD Corporate Tax Statistics (combined statutory CIT rate)", year, True
    except Exception as e:  # noqa: BLE001
        return FALLBACK_TAX, f"Fallback values (OECD fetch failed: {type(e).__name__})", "stored", False


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_yields():
    try:
        tables = pd.read_html(io.StringIO(_get(TE_URL).text))
        yields, dates = {}, []
        for t in tables:
            cols = [str(c) for c in t.columns]
            if "Yield" not in cols:
                continue
            t.columns = cols
            for _, row in t.iterrows():
                name = str(row[cols[0]]).strip()
                name = TE_ALIASES.get(name, name)
                try:
                    y = float(str(row["Yield"]).replace("%", ""))
                except ValueError:
                    continue
                yields.setdefault(name, y)  # first table = 10Y majors
                if "Date" in cols:
                    dates.append(str(row["Date"]))
        if "United States" not in yields:
            raise ValueError("US yield missing")
        out = {n: yields.get(n, FALLBACK_YIELDS.get(n)) for n in COUNTRIES}
        as_of = f"{date.today()} (quote time {dates[0]})" if dates else str(date.today())
        return out, "Trading Economics, 10-year government bond yields", as_of, True
    except Exception as e:  # noqa: BLE001
        return FALLBACK_YIELDS, f"Fallback values (Trading Economics fetch failed: {type(e).__name__})", "stored", False


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def main():
    st.set_page_config(page_title="International Cost of Capital", layout="wide")
    st.title("International Cost of Capital")
    st.caption("Cost of equity, cost of debt and WACC for a project in USD and in a selected country's currency.")

    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

    with st.spinner("Fetching data…"):
        betas, beta_src, beta_date, beta_live = fetch_betas()
        bbb, bbb_src, bbb_date, bbb_live = fetch_bbb_spread()
        taxes, tax_src, tax_date, tax_live = fetch_tax_rates()
        ylds, y_src, y_date, y_live = fetch_yields()

    # ---------------- Sidebar inputs ----------------
    sb = st.sidebar
    sb.header("Project settings")
    industries = sorted(betas.keys())
    industry = sb.selectbox("Industry", industries,
                            index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
    countries = list(COUNTRIES.keys())
    country = sb.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))
    ccy = COUNTRIES[country][1]
    erp = sb.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1) / 100
    debt_share = sb.slider("Target debt share, D/(D+E) (%)", 0.0, 90.0, DEFAULT_DEBT, 1.0) / 100
    tax_choice = sb.radio("Project tax rate", ["U.S.", "Selected country"], horizontal=True)

    sb.header("Manual overrides")

    def override(label, fetched, fmt="%.2f", step=0.01, key=None):
        on = sb.checkbox(f"Override {label}", key=f"ov_{key}")
        if on:
            return sb.number_input(label, value=float(fetched), step=step, format=fmt, key=f"val_{key}"), True
        sb.caption(f"{label}: {fetched:.2f} (fetched)")
        return float(fetched), False

    beta_u, beta_ov = override("Unlevered beta", betas[industry], key="beta")
    tax_us, tax_us_ov = override("U.S. corporate tax rate (%)", taxes["United States"], key="taxus")
    tax_fc, tax_fc_ov = override(f"{country} corporate tax rate (%)", taxes[country], key="taxfc")
    spread_usd, spr_ov = override("USD BBB spread (%)", bbb, key="spr")
    y_us, y_us_ov = override("U.S. 10Y government bond yield (%)", ylds["United States"], key="yus")
    y_fc, y_fc_ov = override(f"{country} 10Y government bond yield (%)", ylds[country], key="yfc")

    tax = (tax_us if tax_choice == "U.S." else tax_fc) / 100
    rf_usd, rf_fc, s_usd = y_us / 100, y_fc / 100, spread_usd / 100

    # Foreign-currency spread: auto unless overridden
    auto = compute_cost_of_capital(beta_u, tax, debt_share, erp, rf_usd, rf_fc, s_usd)
    fc_spread_on = sb.checkbox(f"Override {ccy} BBB spread", key="ov_sfc")
    if fc_spread_on:
        s_fc = sb.number_input(f"{ccy} BBB spread (%)", value=round(auto["spread_fc_auto"] * 100, 2),
                               step=0.01, format="%.2f", key="val_sfc") / 100
    else:
        s_fc = None
        sb.caption(f"{ccy} BBB spread: {auto['spread_fc_auto'] * 100:.2f} (auto-calculated)")

    res = compute_cost_of_capital(beta_u, tax, debt_share, erp, rf_usd, rf_fc, s_usd, s_fc)

    # ---------------- Results ----------------
    st.subheader(f"{industry} — {country} ({ccy})")
    c1, c2, c3 = st.columns(3)
    c1.metric("Levered beta", f"{res['levered_beta']:.3f}")
    c2.metric("Project tax rate", pct(tax))
    c3.metric("Debt share / D/E", f"{debt_share * 100:.0f}% / {debt_share / (1 - debt_share):.3f}")

    summary = pd.DataFrame({
        "Item": ["10Y government bond yield (risk-free)", "Cost of equity", "BBB spread",
                 "Pre-tax cost of debt", "After-tax cost of debt", "WACC"],
        "USD": [pct(rf_usd), pct(res["re_usd"]), pct(s_usd), pct(res["rd_usd"]),
                pct(res["rd_usd_after_tax"]), pct(res["wacc_usd"])],
        ccy if ccy != "USD" else "USD (selected country)":
            [pct(rf_fc), pct(res["re_fc"]), pct(res["spread_fc"]) + (" (manual)" if fc_spread_on else " (auto)"),
             pct(res["rd_fc"]), pct(res["rd_fc_after_tax"]), pct(res["wacc_fc"])],
    })
    st.table(summary.set_index("Item"))

    m1, m2 = st.columns(2)
    m1.metric("WACC (USD)", pct(res["wacc_usd"]))
    m2.metric(f"WACC ({ccy})", pct(res["wacc_fc"]), delta=f"{(res['wacc_fc'] - res['wacc_usd']) * 100:+.2f} pp vs USD",
              delta_color="off")

    # ---------------- Sources ----------------
    st.subheader("Data sources and dates")
    src = pd.DataFrame([
        ["Unlevered beta", f"{betas[industry]:.2f}", beta_src, beta_date, "Manual" if beta_ov else ("Live" if beta_live else "Fallback")],
        ["USD BBB spread (%)", f"{bbb:.2f}", bbb_src, bbb_date, "Manual" if spr_ov else ("Live" if bbb_live else "Fallback")],
        ["U.S. tax rate (%)", f"{taxes['United States']:.2f}", tax_src, tax_date, "Manual" if tax_us_ov else ("Live" if tax_live else "Fallback")],
        [f"{country} tax rate (%)", f"{taxes[country]:.2f}", tax_src, tax_date, "Manual" if tax_fc_ov else ("Live" if tax_live else "Fallback")],
        ["U.S. 10Y yield (%)", f"{ylds['United States']:.2f}", y_src, y_date, "Manual" if y_us_ov else ("Live" if y_live else "Fallback")],
        [f"{country} 10Y yield (%)", f"{ylds[country]:.2f}", y_src, y_date, "Manual" if y_fc_ov else ("Live" if y_live else "Fallback")],
    ], columns=["Input", "Fetched value", "Source", "As of", "Value used"])
    st.dataframe(src, hide_index=True, use_container_width=True)
    if not all([beta_live, bbb_live, tax_live, y_live]):
        st.warning("At least one source could not be reached, so stored fallback values are shown. "
                   "Use Refresh data or the manual overrides.")

    # ---------------- Assumptions ----------------
    with st.expander("Main assumptions and formulas", expanded=False):
        st.markdown(f"""
- **Levered beta (Hamada):** β_L = β_U × [1 + (1 − t) × D/E], with D/E = w_D / (1 − w_D).
- **USD cost of equity (CAPM):** r_E,USD = y_US + β_L × ERP. The U.S. 10-year Treasury yield is the USD risk-free rate.
- **USD cost of debt:** r_D,USD = y_US + BBB spread (Damodaran, large non-financial firms).
- **Converting to {ccy}:** r_{ccy} = (1 + r_USD) × (1 + y_{ccy}) / (1 + y_US) − 1, using the 10-year government bond yields
  (relative interest rate parity / Fisher). Both currencies therefore carry the same real required return.
- **{ccy} cost of debt:** by default the converted USD cost of debt (auto spread = r_D,{ccy} − y_{ccy}).
  With the override on, r_D,{ccy} = y_{ccy} + manual spread. The override never changes USD results.
- **WACC:** (1 − w_D) × r_E + w_D × r_D × (1 − t), with the same project tax rate, debt share and ERP in both currencies.
- Betas are U.S. industry betas applied to the foreign project; the government bond yield is treated as risk-free
  (no separate country risk premium beyond what the yield gap implies).
""")

    # ---------------- All-industry comparison ----------------
    st.subheader("All-industry comparison")
    st.caption("Same country, tax rate, ERP, debt share, yields and spreads; each industry uses its fetched unlevered beta.")
    rows = []
    for ind, bu in betas.items():
        r = compute_cost_of_capital(bu, tax, debt_share, erp, rf_usd, rf_fc, s_usd, s_fc)
        rows.append({"Industry": ind, "Unlevered beta": round(bu, 3), "Levered beta": round(r["levered_beta"], 3),
                     "Cost of equity USD (%)": round(r["re_usd"] * 100, 2),
                     f"Cost of equity {ccy} (%)": round(r["re_fc"] * 100, 2),
                     "WACC USD (%)": round(r["wacc_usd"] * 100, 2),
                     f"WACC {ccy} (%)": round(r["wacc_fc"] * 100, 2)})
    all_ind = pd.DataFrame(rows).sort_values("WACC USD (%)", ascending=False)
    st.dataframe(all_ind, hide_index=True, use_container_width=True, height=400)

    # ---------------- Downloads ----------------
    st.subheader("Download results")
    inputs = pd.DataFrame({
        "Input": ["Industry", "Country", "Currency", "Unlevered beta", "Project tax rate (%)", "Tax rate basis",
                  "ERP (%)", "Debt share (%)", "U.S. 10Y yield (%)", f"{country} 10Y yield (%)",
                  "USD BBB spread (%)", f"{ccy} BBB spread (%)", "Levered beta",
                  "Cost of equity USD (%)", f"Cost of equity {ccy} (%)", "Pre-tax cost of debt USD (%)",
                  f"Pre-tax cost of debt {ccy} (%)", "WACC USD (%)", f"WACC {ccy} (%)"],
        "Value": [industry, country, ccy, beta_u, tax * 100, tax_choice, erp * 100, debt_share * 100, y_us, y_fc,
                  spread_usd, res["spread_fc"] * 100, res["levered_beta"], res["re_usd"] * 100, res["re_fc"] * 100,
                  res["rd_usd"] * 100, res["rd_fc"] * 100, res["wacc_usd"] * 100, res["wacc_fc"] * 100],
    })
    d1, d2 = st.columns(2)
    d1.download_button("Download selected result (CSV)", inputs.to_csv(index=False).encode(),
                       file_name=f"cost_of_capital_{country.replace(' ', '_')}.csv", mime="text/csv")
    d2.download_button("Download all-industry comparison (CSV)", all_ind.to_csv(index=False).encode(),
                       file_name=f"all_industries_{country.replace(' ', '_')}.csv", mime="text/csv")


if __name__ == "__main__":
    main()
