import io
import os
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

APP_VERSION = "fx_rate8_mirror_csv_first_print_fix_2026-03-02"

# GitHub上の quote.csv（raw）を参照
RAW_QUOTE_URL = "https://raw.githubusercontent.com/oikawa4126/fx_rate_app/main/quote.csv"

TARGET_CCYS = ["USD", "EUR", "GBP", "AUD", "SGD", "THB"]

SPREAD_BY_CCY_JPY = {
    "USD": 1.00,
    "EUR": 1.40,
    "GBP": 4.00,
    "AUD": 2.50,
    "SGD": 0.83,
    "THB": 8.00,  # 100THBあたり
}
HUNDRED_UNIT_SPREAD = {"THB"}

def get_spread_per_unit(ccy: str) -> float:
    s = float(SPREAD_BY_CCY_JPY.get(ccy.upper(), 0.0))
    return s / 100.0 if ccy.upper() in HUNDRED_UNIT_SPREAD else s

if "result" not in st.session_state:
    st.session_state["result"] = None
if "do_print" not in st.session_state:
    st.session_state["do_print"] = False

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
      display:block !important;
      position:fixed; left:0; top:0;
      width:calc(100% - 32mm);
      height:140mm;
      overflow:hidden;
      page-break-after:avoid;
      page-break-inside:avoid;
    }
    header, footer { display:none !important; }
  }
</style>
"""
st.markdown(PRINT_CSS, unsafe_allow_html=True)

def looks_like_date(s: str) -> bool:
    try:
        pd.to_datetime(s, errors="raise")
        return True
    except Exception:
        return False

def parse_quote_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), encoding="shift_jis", header=None)

    header_idx = None
    scan = min(len(raw), 40)
    for i in range(scan):
        row = [str(x).strip() for x in raw.iloc[i].tolist()]
        tokens = set(row)
        score = sum(1 for t in ["USD", "EUR", "GBP"] if t in tokens)
        next_is_date = (i + 1 < scan) and looks_like_date(str(raw.iloc[i + 1, 0]).strip())
        if score >= 2 and next_is_date:
            header_idx = i
            break

    if header_idx is None:
        df = pd.read_csv(io.StringIO(text), encoding="shift_jis")
        cols = [str(c).strip() for c in df.columns]
        if not cols or cols[0].upper() != "DATE":
            cols[0] = "DATE"
        df.columns = cols
    else:
        df = pd.read_csv(io.StringIO(text), encoding="shift_jis", header=header_idx)
        cols = [str(c).strip() for c in df.columns]
        cols[0] = "DATE"
        df.columns = cols

    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    return df.dropna(subset=["DATE"]).reset_index(drop=True)

def load_quote_df_from_github() -> pd.DataFrame:
    r = requests.get(RAW_QUOTE_URL, timeout=25)
    r.raise_for_status()
    text = r.content.decode("shift_jis", errors="ignore")
    return parse_quote_csv(text)

def resolve_rate_column(df: pd.DataFrame, ccy: str) -> str:
    c = ccy.upper()
    for name in (c, f"{c}.1"):
        if name in df.columns:
            return name
    available = ", ".join([col for col in df.columns if col != "DATE"])
    raise KeyError(f"{ccy} 列が見つかりません。CSV上の列名: {available}")

def adjust_to_next_business_day(dates: set[date], end_d: date) -> date:
    d = end_d
    for _ in range(7):
        if d in dates:
            return d
        d += timedelta(days=1)
    return end_d

def get_avg_ttm_simple(df: pd.DataFrame, ccy: str, start_d: date, end_d: date) -> float:
    col = resolve_rate_column(df, ccy)
    tmp = df[["DATE", col]].copy()
    tmp["DATE_ONLY"] = tmp["DATE"].dt.date
    tmp["TTM"] = pd.to_numeric(tmp[col], errors="coerce")
    sel = tmp.loc[(tmp["DATE_ONLY"] >= start_d) & (tmp["DATE_ONLY"] <= end_d), "TTM"].dropna()
    if sel.empty:
        raise ValueError("指定期間にTTMがありません")
    return float(sel.mean())

def build_print_html(start_d: date, end_d: date, ccy: str, avg: float, note: str) -> str:
    fixed_sentence = "レートの証明として精算書にこの書面を添付してください。"
    return "\n".join([
        '<div class="print-sheet"><div>',
        '<h2 style="margin:0 0 8mm 0;">出張期間の平均レート</h2>',
        f"<div>開始日（出発日）</div><div>{start_d:%Y/%m/%d}</div>",
        f"<div style='margin-top:4mm;'>終了日（帰着日）</div><div>{end_d:%Y/%m/%d}</div>",
        f"<div style='margin-top:4mm;'>外貨（対JPY）</div><div>{ccy}</div>",
        f"<div style='margin-top:10mm;font-size:22pt;font-weight:700;'>平均TTS（円） {avg:,.2f}</div>",
        f"<div style='margin-top:6mm;font-size:10pt;'>{note}</div>",
        f"<div style='margin-top:6mm;font-size:10pt;'>{fixed_sentence}</div>",
        "</div></div>"
    ])

st.title("出張期間の平均レート")
with st.expander("（確認用）稼働情報", expanded=False):
    st.write("APP_VERSION:", APP_VERSION)

today = date.today()
c1, c2 = st.columns(2)
with c1:
    start_date = st.date_input("開始日（出発日）", today - timedelta(days=30))
with c2:
    end_date = st.date_input("終了日（帰着日）", today)

st.caption("プルダウンに出ない通貨の場合には、メールで経理U及川宛てにレートの問い合わせをしてください")
foreign = st.selectbox("外貨（対JPY）", TARGET_CCYS, index=0)

if st.button("平均レート計算"):
    try:
        df = load_quote_df_from_github()
        dates = set(df["DATE"].dt.date)
        adjusted_end = adjust_to_next_business_day(dates, end_date)

        note = ""
        if adjusted_end != end_date:
            note = f"帰着日に公表が無かったため、翌営業日の {adjusted_end:%Y-%m-%d} までで平均を計算します。"
            st.info(note)

        avg_ttm = get_avg_ttm_simple(df, foreign, start_date, adjusted_end)
        avg_tts = round(avg_ttm + get_spread_per_unit(foreign), 2)

        st.session_state["result"] = {"start": start_date, "end": adjusted_end, "ccy": foreign, "avg": avg_tts, "note": note}
    except Exception as e:
        st.error(str(e))

res = st.session_state.get("result")
if res:
    st.metric("平均TTS（円）", f"{res['avg']:,.2f}")
    if st.button("平均レート印刷"):
        st.session_state["do_print"] = True
    st.markdown(build_print_html(res["start"], res["end"], res["ccy"], res["avg"], res["note"]), unsafe_allow_html=True)

if st.session_state.get("do_print"):
    components.html("<script>parent.window.print()</script>", height=0, scrolling=False)
    st.session_state["do_print"] = False
