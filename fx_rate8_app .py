# fx_rate8_app.py（KRW対応版：全文置換）
# - みずほ quote.csv（TTM）→ 期間平均TTM → スプレッド加算で平均TTS
# - THB: スプレッド 100THBあたり8円 → /100補正
# - KRW: レートが KRW(100)（100通貨単位）→ /100補正、スプレッドも 100通貨あたり0.2円 → /100補正
# - 帰着日に公表が無い場合は翌営業日に補正（最大+7日）、メッセージは画面＆印刷に反映
# - 印刷は「印刷用レイアウトのみ」A4縦上半分、白地黒文字
# - Streamlit Cloud 上で社内プロキシ(proxy2...)を絶対に使わない（trust_env=False＋proxies無効）
# - みずほCSVのヘッダー行を自動検出（Unnamed問題を解消）
# - 三重引用 f-string を使わず format で印刷HTMLを生成（貼り付け事故対策）

import io
import os
from datetime import date, timedelta
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

APP_VERSION = "fx_rate8_cloud_fix_headers_no_proxy_KRW_2026-02-16"

MIZUHO_CSV_URL = "https://www.mizuhobank.co.jp/market/quote.csv"

# ▼ 通貨（KRW追加）
TARGET_CCYS = ["USD", "EUR", "GBP", "AUD", "SGD", "THB", "KRW"]

# ▼ スプレッド（円）
# THB: 100THBあたり8円
# KRW: 100KRWあたり0.2円（指定どおり）
SPREAD_BY_CCY_JPY = {
    "USD": 1.00,
    "EUR": 1.40,
    "GBP": 4.00,
    "AUD": 2.50,
    "SGD": 0.83,
    "THB": 8.00,   # 100THBあたり
    "KRW": 0.20,   # 100KRWあたり ★追加
}

# 「100通貨あたりスプレッド」扱い
HUNDRED_UNIT_SPREAD = {"THB", "KRW"}

def get_spread_per_unit(ccy: str) -> float:
    s = float(SPREAD_BY_CCY_JPY.get(ccy.upper(), 0.0))
    return s / 100.0 if ccy.upper() in HUNDRED_UNIT_SPREAD else s

# session_state
if "result" not in st.session_state:
    st.session_state["result"] = None
if "do_print" not in st.session_state:
    st.session_state["do_print"] = False

# 印刷CSS（印刷は印刷ブロックのみ／白地黒文字／A4上半分）
PRINT_CSS = r"""
<style>
  .print-sheet { display: none; }

  @media print {
    @page { size: A4; margin: 16mm; }

    body * { visibility: hidden !important; }
    .print-sheet, .print-sheet * { visibility: visible !important; }

    html, body, .stApp, .stApp * { background:#fff !important; color:#000 !important; }
    * {
      -webkit-text-fill-color:#000 !important;
      text-shadow:none !important;
      filter:none !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }

    .print-sheet{
      display:block !important;
      position:fixed; left:0; top:0;
      width:calc(100% - 32mm);
      height:140mm;
      overflow:hidden;
      page-break-after:avoid;
      page-break-inside:avoid;
    }

    .sheet-box{ width:165mm; max-width:100%; margin:0 auto; padding-top:6mm; box-sizing:border-box; }
    .sheet-title{ font-size:22pt; font-weight:700; margin:0 0 10mm 0; }
    .sheet-grid{ display:grid; grid-template-columns:1fr 1fr; column-gap:12mm; row-gap:4mm; font-size:11pt; }
    .sheet-field label{ display:block; font-size:9.5pt; margin-bottom:1.5mm; }
    .sheet-boxed{ border:none !important; padding:0 !important; margin:0 !important; background:transparent !important; }

    .sheet-result-label{ margin-top:10mm; font-size:10pt; font-weight:600; }
    .sheet-result-value{ font-size:22pt; font-weight:800; margin-top:2mm; }
    .sheet-adjust-note{ margin-top:6mm; font-size:9pt; }
    .sheet-note{ margin-top:6mm; font-size:9pt; }

    header, footer { display:none !important; }
  }
</style>
"""
st.markdown(PRINT_CSS, unsafe_allow_html=True)

# ========= Cloudでproxyを完全に無視するための処理 =========
def _purge_proxy_env():
    for k in [
        "HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy",
        "ALL_PROXY","all_proxy","NO_PROXY","no_proxy"
    ]:
        os.environ.pop(k, None)

def download_quote_csv_text() -> str:
    _purge_proxy_env()
    s = requests.Session()
    s.trust_env = False
    r = s.get(
        MIZUHO_CSV_URL,
        timeout=25,
        proxies={"http": None, "https": None},
    )
    r.raise_for_status()
    return r.content.decode("shift_jis", errors="ignore")

def _looks_like_date(s: str) -> bool:
    try:
        pd.to_datetime(s, errors="raise")
        return True
    except Exception:
        return False

def parse_quote_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), encoding="shift_jis", header=None)

    header_idx = None
    scan_rows = min(len(raw), 40)
    for i in range(scan_rows):
        row = [str(x).strip() for x in raw.iloc[i].tolist()]
        tokens = set(row)

        # USD/EUR等が複数含まれる行をヘッダ候補にする（KRWがあってもなくてもOK）
        score = sum(1 for t in ["USD", "EUR", "GBP"] if t in tokens)

        next_is_date = False
        if i + 1 < scan_rows:
            next_first = str(raw.iloc[i + 1, 0]).strip()
            next_is_date = _looks_like_date(next_first)

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
    df = df.dropna(subset=["DATE"]).reset_index(drop=True)
    return df

def load_quote_df() -> pd.DataFrame:
    text = download_quote_csv_text()
    return parse_quote_csv(text)

# ===== 列名解決（KRWは KRW(100) を見る） =====
HUNDRED_UNIT_RATE_CCYS = {"KRW"}  # 100通貨単位の“レート列”を持つ通貨

def resolve_rate_column(df: pd.DataFrame, ccy: str) -> str:
    c = ccy.upper()
    if c in HUNDRED_UNIT_RATE_CCYS:
        candidates = [f"{c}(100)", f"{c}(100).1", c, f"{c}.1"]
    else:
        candidates = [c, f"{c}.1"]

    for name in candidates:
        if name in df.columns:
            return name

    available = ", ".join([col for col in df.columns if col != "DATE"])
    raise KeyError(f"{ccy} 列が見つかりません。CSV上の列名: {available}")

# ===== 計算関数 =====
def adjust_to_next_business_day(available_dates: set[date], end_date: date) -> date:
    d = end_date
    for _ in range(7):
        if d in available_dates:
            return d
        d += timedelta(days=1)
    return end_date

def get_avg_ttm_simple(df: pd.DataFrame, ccy: str, start_d: date, end_d: date) -> float:
    col = resolve_rate_column(df, ccy)

    tmp = df[["DATE", col]].copy()
    tmp["DATE_ONLY"] = tmp["DATE"].dt.date
    tmp["TTM_RAW"] = pd.to_numeric(tmp[col], errors="coerce")

    mask = (tmp["DATE_ONLY"] >= start_d) & (tmp["DATE_ONLY"] <= end_d)
    sel = tmp.loc[mask, "TTM_RAW"].dropna()

    if sel.empty:
        raise ValueError(f"{start_d}〜{end_d} に {ccy} のTTMが見つかりません。")

    avg = float(sel.mean())

    # KRW(100) は 100通貨あたりなので 1通貨あたりへ /100
    if ccy.upper() in HUNDRED_UNIT_RATE_CCYS and "(100)" in col:
        avg = avg / 100.0

    return avg

def build_print_html(start_d: date, end_d: date, ccy: str, avg: float, note: str) -> str:
    lines = [
        '<div class="print-sheet">',
        '  <div class="sheet-box">',
        '    <div class="sheet-title">出張期間の平均レート</div>',
        '    <div class="sheet-grid">',
        '      <div class="sheet-field"><label>開始日（出発日）</label><div class="sheet-boxed">{start}</div></div>',
        '      <div class="sheet-field"><label>終了日（帰着日）</label><div class="sheet-boxed">{end}</div></div>',
        '      <div class="sheet-field" style="grid-column: 1 / span 2;"><label>外貨（対JPY）</label><div class="sheet-boxed">{ccy}</div></div>',
        '    </div>',
        '    <div class="sheet-result-label">平均TTS（円）</div>',
        '    <div class="sheet-result-value">{avg}</div>',
        '    <div class="sheet-adjust-note">{note}</div>',
        '    <div class="sheet-note">レートの証明として清算書にこの書面を添付してください。</div>',
        '  </div>',
        '</div>',
    ]
    return "\n".join(lines).format(
        start=start_d.strftime("%Y/%m/%d"),
        end=end_d.strftime("%Y/%m/%d"),
        ccy=ccy,
        avg=f"{avg:,.4f}" if ccy.upper() == "KRW" else f"{avg:,.2f}",
        note=note
    )

# ===== UI =====
st.title("出張期間の平均レート")

with st.expander("（確認用）稼働情報", expanded=False):
    st.write("APP_VERSION:", APP_VERSION)

today = date.today()
default_start = today - timedelta(days=30)

c1, c2 = st.columns(2)
with c1:
    start_date = st.date_input("開始日（出発日）", default_start)
with c2:
    end_date = st.date_input("終了日（帰着日）", today)

# 画面だけの注意文（印刷には出ない）
st.caption("プルダウンに出ない通貨の場合には、メールで経理U及川宛てにレートの問い合わせをしてください")

foreign = st.selectbox("外貨（対JPY）", TARGET_CCYS, index=0)

if st.button("平均レート計算"):
    try:
        df = load_quote_df()
        dates_set = set(df["DATE"].dt.date)
        adjusted_end = adjust_to_next_business_day(dates_set, end_date)

        adjust_note = ""
        if adjusted_end != end_date:
            adjust_note = f"帰着日に公表が無かったため、翌営業日の {adjusted_end:%Y-%m-%d} までで平均を計算します。"
            st.info(adjust_note)

        avg_ttm = get_avg_ttm_simple(df, foreign, start_date, adjusted_end)
        spread = get_spread_per_unit(foreign)
        avg_tts = round(avg_ttm + spread, 6 if foreign.upper() == "KRW" else 2)

        st.session_state["result"] = {
            "start": start_date,
            "end": adjusted_end,
            "ccy": foreign,
            "avg": avg_tts,
            "note": adjust_note
        }

    except Exception as e:
        st.error(str(e))
        st.exception(e)

res = st.session_state.get("result")
if res:
    # KRWは桁が小さいので表示桁を増やす
    if res["ccy"].upper() == "KRW":
        st.metric("平均TTS（円）", f"{res['avg']:,.4f}")
    else:
        st.metric("平均TTS（円）", f"{res['avg']:,.2f}")

    if st.button("平均レート印刷"):
        st.session_state["do_print"] = True

    st.markdown(
        build_print_html(res["start"], res["end"], res["ccy"], res["avg"], res["note"]),
        unsafe_allow_html=True
    )

if st.session_state.get("do_print"):
    components.html("<script>parent.window.print()</script>", height=0, scrolling=False)
    st.session_state["do_print"] = False
