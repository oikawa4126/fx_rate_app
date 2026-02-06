# fx_rate8_app.py（IDR除外版：全文置換）
# - みずほ quote.csv（TTMのみ）取得 → 期間平均TTM → スプレッド加算で平均TTS
# - 帰着日に公表が無い場合は翌営業日に補正（最大+7日）、そのメッセージを画面・印刷に反映
# - ブラウザ印刷（PDF保存）：印刷時は印刷用レイアウトだけをA4縦1ページ（上半分）に出力
# - 印刷時は白地・黒文字を強制（反転対策）
# - 注意文は画面のみ（印刷には出さない）
# - THBスプレッド8円は「100THBあたり」→ /100補正
# - ★IDRは計算対象から完全に除外（プルダウン・計算・列解決・スプレッド定義から削除）

import io
from datetime import date, timedelta
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ===== 接続設定 =====
PROXIES = {
    "http": "http://proxy2.delphys.co.jp:8080",
    "https": "http://proxy2.delphys.co.jp:8080",
}
VERIFY = True
MIZUHO_CSV_URL = "https://www.mizuhobank.co.jp/market/quote.csv"

# ===== 対象通貨（IDR除外） =====
TARGET_CCYS = ["USD", "EUR", "GBP", "AUD", "SGD", "THB"]

# ===== スプレッド（円） =====
# THB=8.00 は「100THBあたり8円」→ /100 補正して 1THBあたり0.08円として加算
SPREAD_BY_CCY_JPY = {
    "USD": 1.00,
    "EUR": 1.40,
    "GBP": 4.00,
    "AUD": 2.50,
    "SGD": 0.83,
    "THB": 8.00,   # 100THBあたり
}
HUNDRED_UNIT_SPREAD = {"THB"}  # 100通貨あたりスプレッド扱い

def get_spread_per_unit(ccy: str) -> float:
    s = float(SPREAD_BY_CCY_JPY.get(ccy.upper(), 0.0))
    return s / 100.0 if ccy.upper() in HUNDRED_UNIT_SPREAD else s

# ===== session_state（結果保持・印刷トリガ） =====
if "result" not in st.session_state:
    st.session_state["result"] = None  # {"start","end","ccy","avg","adjust_note"}
if "do_print" not in st.session_state:
    st.session_state["do_print"] = False

# ===== 印刷CSS（印刷は印刷ブロックのみ／白地黒文字／A4上半分） =====
PRINT_CSS = r"""
<style>
  .print-sheet { display: none; }

  @media print {
    @page { size: A4; margin: 16mm; }

    /* 全部隠す：注意文・入力欄・ボタン等は印刷しない */
    body * { visibility: hidden !important; }

    /* 印刷ブロックだけ可視化 */
    .print-sheet, .print-sheet * { visibility: visible !important; }

    /* 白地＋黒文字を強制（反転防止） */
    html, body, .stApp, .stApp * { background: #fff !important; color: #000 !important; }
    * {
      -webkit-text-fill-color:#000 !important;
      text-shadow:none !important;
      filter:none !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }

    /* 1ページ化：上半分固定 */
    .print-sheet{
      display:block !important;
      position:fixed; left:0; top:0;
      width:calc(100% - 32mm);
      height:140mm;
      overflow:hidden;
      page-break-after:avoid;
      page-break-inside:avoid;
    }

    /* test2の“まとまり”寄り */
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

# ===== データ取得・計算 =====
def adjust_to_next_business_day(available_dates: set[date], end_date: date) -> date:
    d = end_date
    for _ in range(7):
        if d in available_dates:
            return d
        d += timedelta(days=1)
    return end_date

def _read_csv_text_to_df(text: str, header=None) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(text), encoding="shift_jis", header=header)

def load_quote_csv_minimal() -> pd.DataFrame:
    r = requests.get(MIZUHO_CSV_URL, proxies=PROXIES, verify=VERIFY, timeout=25)
    r.raise_for_status()
    text = r.content.decode("shift_jis", errors="ignore")

    try:
        df = _read_csv_text_to_df(text, header=0)
    except Exception:
        df = _read_csv_text_to_df(text, header=None)

    cols = [str(c).strip() for c in df.columns]
    if not cols or cols[0].upper() != "DATE":
        cols[0] = "DATE"
    df.columns = cols

    # Unnamed大量時はヘッダ推定（対象はTARGET_CCYSのみ）
    too_many_unnamed = sum(1 for c in df.columns if str(c).lower().startswith("unnamed")) >= max(2, len(df.columns)//2)
    if too_many_unnamed:
        tmp = _read_csv_text_to_df(text, header=None)
        head_n = min(len(tmp), 10)
        header_idx = None
        for i in range(head_n):
            row = [str(x).strip() for x in tmp.iloc[i].tolist()]
            tokens = set(row)
            score = sum(1 for t in TARGET_CCYS if t in tokens)
            if i + 1 < head_n:
                next_first = str(tmp.iloc[i+1, 0]).strip()
                try:
                    _ = pd.to_datetime(next_first, errors="raise")
                    score += 1
                except Exception:
                    pass
            if score >= 2:
                header_idx = i
                break
        if header_idx is not None:
            df = _read_csv_text_to_df(text, header=header_idx)
            cols = [str(c).strip() for c in df.columns]
            cols[0] = "DATE"
            df.columns = cols

    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    df = df.dropna(subset=["DATE"]).reset_index(drop=True)
    return df

# ===== 列名解決（IDRは除外。THBは通常列で入る前提） =====
def resolve_rate_column(df: pd.DataFrame, ccy: str) -> str:
    c = ccy.upper()
    candidates = [c, f"{c}.1"]  # 重複列への保険
    for name in candidates:
        if name in df.columns:
            return name
    available = ", ".join([col for col in df.columns if col != "DATE"])
    raise KeyError(f"{ccy} 列が見つかりません。CSV上の列名: {available}")

def get_avg_ttm_simple(df: pd.DataFrame, ccy: str, start_d: date, end_d: date) -> float:
    col = resolve_rate_column(df, ccy)
    tmp = df[["DATE", col]].copy()
    tmp["DATE_ONLY"] = tmp["DATE"].dt.date
    tmp["VAL"] = pd.to_numeric(tmp[col], errors="coerce")
    mask = (tmp["DATE_ONLY"] >= start_d) & (tmp["DATE_ONLY"] <= end_d)
    sel = tmp.loc[mask, "VAL"].dropna()
    if sel.empty:
        non_null = tmp.dropna(subset=["VAL"])
        if non_null.empty:
            raise ValueError(f"{ccy} の列（{col}）は存在しますが、数値データが見つかりませんでした。")
        first = non_null["DATE_ONLY"].iloc[0]
        last = non_null["DATE_ONLY"].iloc[-1]
        raise ValueError(f"{start_d}〜{end_d} に {ccy} のTTMが見つかりません。{ccy}データの存在範囲：{first}〜{last}（列={col}）")
    return float(sel.mean())

def build_print_html(start_d: date, end_d: date, ccy: str, avg: float, adjust_note: str) -> str:
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
        '    <div class="sheet-adjust-note">{adjust_note}</div>',
        '    <div class="sheet-note">レートの証明として清算書にこの書面を添付してください。</div>',
        '  </div>',
        '</div>',
    ]
    return "\n".join(lines).format(
        start=start_d.strftime("%Y/%m/%d"),
        end=end_d.strftime("%Y/%m/%d"),
        ccy=ccy,
        avg=f"{avg:,.2f}",
        adjust_note=adjust_note
    )

# ===== UI =====
st.title("出張期間の平均レート")

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
    if start_date > end_date:
        st.error("終了日は開始日より後にしてください。")
        st.stop()

    try:
        wide_df = load_quote_csv_minimal()
        dates_set = set(wide_df["DATE"].dt.date)
        adjusted_end = adjust_to_next_business_day(dates_set, end_date)

        adjust_note = ""
        if adjusted_end != end_date:
            adjust_note = f"帰着日に公表が無かったため、翌営業日の {adjusted_end:%Y-%m-%d} までで平均を計算します。"
            st.info(adjust_note)

        avg_ttm = get_avg_ttm_simple(wide_df, foreign, start_date, adjusted_end)
        spread = get_spread_per_unit(foreign)
        avg_tts = round(avg_ttm + spread, 2)

        st.session_state["result"] = {
            "start": start_date,
            "end": adjusted_end,
            "ccy": foreign,
            "avg": avg_tts,
            "adjust_note": adjust_note
        }
    except Exception as e:
        st.error(str(e))

res = st.session_state.get("result")
if res:
    st.metric("平均TTS（円）", f"{res['avg']:,.2f}")

    if st.button("平均レート印刷"):
        st.session_state["do_print"] = True

    st.markdown(
        build_print_html(res["start"], res["end"], res["ccy"], res["avg"], res["adjust_note"]),
        unsafe_allow_html=True
    )

if st.session_state.get("do_print"):
    components.html("<script>parent.window.print()</script>", height=0, scrolling=False)
    st.session_state["do_print"] = False