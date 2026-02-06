# fx_rate8_app.py（Streamlit Cloud対応：社内プロキシを自動で使い分け）
import io
from datetime import date, timedelta
import os
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

MIZUHO_CSV_URL = "https://www.mizuhobank.co.jp/market/quote.csv"

TARGET_CCYS = ["USD", "EUR", "GBP", "AUD", "SGD", "THB"]

SPREAD_BY_CCY_JPY = {
    "USD": 1.00, "EUR": 1.40, "GBP": 4.00, "AUD": 2.50, "SGD": 0.83,
    "THB": 8.00,  # 100THBあたり
}
HUNDRED_UNIT_SPREAD = {"THB"}

def get_spread_per_unit(ccy: str) -> float:
    s = float(SPREAD_BY_CCY_JPY.get(ccy.upper(), 0.0))
    return s / 100.0 if ccy.upper() in HUNDRED_UNIT_SPREAD else s

# --- ここが重要：プロキシ設定を「存在するときだけ」使う ---
def get_requests_kwargs():
    """
    社内PC：環境変数（またはst.secrets）にプロキシが入っていればそれを使う
    Streamlit Cloud：プロキシが無いので None → 直アクセス
    """
    # 1) secrets（CloudのUIで設定できる）優先
    proxy_http = st.secrets.get("PROXY_HTTP", None) if hasattr(st, "secrets") else None
    proxy_https = st.secrets.get("PROXY_HTTPS", None) if hasattr(st, "secrets") else None
    verify = st.secrets.get("REQUESTS_VERIFY", True) if hasattr(st, "secrets") else True

    # 2) 無ければ環境変数
    if proxy_http is None:
        proxy_http = os.environ.get("PROXY_HTTP")
    if proxy_https is None:
        proxy_https = os.environ.get("PROXY_HTTPS")
    if "REQUESTS_VERIFY" in os.environ:
        v = os.environ.get("REQUESTS_VERIFY", "true").lower()
        verify = (v == "true")

    proxies = None
    if proxy_http or proxy_https:
        proxies = {}
        if proxy_http:
            proxies["http"] = proxy_http
        if proxy_https:
            proxies["https"] = proxy_https

    # requests.get に渡す kwargs
    kwargs = {"timeout": 25, "verify": verify}
    if proxies:
        kwargs["proxies"] = proxies
    return kwargs

def load_quote_csv_minimal() -> pd.DataFrame:
    kwargs = get_requests_kwargs()
    r = requests.get(MIZUHO_CSV_URL, **kwargs)
    r.raise_for_status()
    text = r.content.decode("shift_jis", errors="ignore")

    try:
        df = pd.read_csv(io.StringIO(text), encoding="shift_jis")
    except Exception:
        df = pd.read_csv(io.StringIO(text), encoding="shift_jis", header=None)

    cols = [str(c).strip() for c in df.columns]
    if not cols or cols[0].upper() != "DATE":
        cols[0] = "DATE"
    df.columns = cols

    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    df = df.dropna(subset=["DATE"]).reset_index(drop=True)
    return df

def adjust_to_next_business_day(available_dates: set[date], end_date: date) -> date:
    d = end_date
    for _ in range(7):
        if d in available_dates:
            return d
        d += timedelta(days=1)
    return end_date

def get_avg_ttm_simple(df: pd.DataFrame, ccy: str, start_d: date, end_d: date) -> float:
    if ccy not in df.columns:
        available = ", ".join([c for c in df.columns if c != "DATE"])
        raise KeyError(f"{ccy} 列が見つかりません。CSV上の列名: {available}")

    tmp = df[["DATE", ccy]].copy()
    tmp["DATE_ONLY"] = tmp["DATE"].dt.date
    tmp["TTM"] = pd.to_numeric(tmp[ccy], errors="coerce")

    mask = (tmp["DATE_ONLY"] >= start_d) & (tmp["DATE_ONLY"] <= end_d)
    sel = tmp.loc[mask, "TTM"].dropna()
    if sel.empty:
        raise ValueError(f"{start_d}〜{end_d} に {ccy} のTTMが見つかりません。")
    return float(sel.mean())

# ===== 印刷CSS（印刷は印刷ブロックのみ／白地黒文字／A4上半分）=====
PRINT_CSS = r"""
<style>
  .print-sheet { display: none; }
  @media print {
    @page { size: A4; margin: 16mm; }
    body * { visibility: hidden !important; }
    .print-sheet, .print-sheet * { visibility: visible !important; }
    html, body, .stApp, .stApp * { background:#fff !important; color:#000 !important; }
    * { -webkit-text-fill-color:#000 !important; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
    .print-sheet{
      display:block !important; position:fixed; left:0; top:0;
      width:calc(100% - 32mm); height:140mm; overflow:hidden;
      page-break-after:avoid; page-break-inside:avoid;
    }
  }
</style>
"""
st.markdown(PRINT_CSS, unsafe_allow_html=True)

# ===== UI =====
st.title("出張期間の平均レート")
today = date.today()
default_start = today - timedelta(days=30)
c1, c2 = st.columns(2)
with c1:
    start_date = st.date_input("開始日（出発日）", default_start)
with c2:
    end_date = st.date_input("終了日（帰着日）", today)

foreign = st.selectbox("外貨（対JPY）", TARGET_CCYS, index=0)

if st.button("平均レート計算"):
    try:
        df = load_quote_csv_minimal()
        dates_set = set(df["DATE"].dt.date)
        adjusted_end = adjust_to_next_business_day(dates_set, end_date)

        adjust_note = ""
        if adjusted_end != end_date:
            adjust_note = f"帰着日に公表が無かったため、翌営業日の {adjusted_end:%Y-%m-%d} までで平均を計算します。"
            st.info(adjust_note)

        avg_ttm = get_avg_ttm_simple(df, foreign, start_date, adjusted_end)
        spread = get_spread_per_unit(foreign)
        avg_tts = round(avg_ttm + spread, 2)

        st.session_state["result"] = {"start": start_date, "end": adjusted_end, "ccy": foreign, "avg": avg_tts, "note": adjust_note}
    except Exception as e:
        st.error(str(e))

res = st.session_state.get("result")
if res:
    st.metric("平均TTS（円）", f"{res['avg']:,.2f}")

    if st.button("平均レート印刷"):
        st.session_state["do_print"] = True

    st.markdown(
        f"""
        <div class="print-sheet">
          <div>
            <h2 style="margin:0 0 8mm 0;">出張期間の平均レート</h2>
            <div>開始日：{res['start']:%Y/%m/%d}</div>
            <div>終了日：{res['end']:%Y/%m/%d}</div>
            <div>通貨：{res['ccy']}</div>
            <div style="margin-top:6mm;font-size:20pt;font-weight:700;">平均TTS：{res['avg']:,.2f} 円</div>
            <div style="margin-top:6mm;font-size:10pt;">{res['note']}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

if st.session_state.get("do_print"):
    components.html("<script>parent.window.print()</script>", height=0, scrolling=False)
    st.session_state["do_print"] = False