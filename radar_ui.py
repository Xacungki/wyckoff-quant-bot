import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# CHÍNH NHỜ LỆNH NÀY MÀ FILE UI RẤT NHỎ GỌN (Gọi não bộ từ quant_core sang)
from quant_core import QuantDataFetcher, WyckoffVSASignal

# ==========================================
# 1. CẤU HÌNH GIAO DIỆN
# ==========================================
st.set_page_config(page_title="Wyckoff Quant Radar PRO", layout="wide", page_icon="📡")
st.markdown("""<style>.stApp { background-color: #FAF9F6; color: #333333; } h1 { color: #1A1A1A; border-bottom: 2px solid #D4AF37; padding-bottom: 10px; } .stDataFrame { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); border-radius: 8px; } div[data-testid="stMetricValue"] { color: #D4AF37; }</style>""", unsafe_allow_html=True)

SECTORS = {
    "🏦 Ngân hàng": ["VCB.VN", "BID.VN", "CTG.VN", "TCB.VN", "VPB.VN", "MBB.VN", "ACB.VN", "STB.VN", "HDB.VN", "VIB.VN", "TPB.VN", "SHB.VN", "EIB.VN", "MSB.VN", "OCB.VN", "LPB.VN"],
    "🏢 Bất động sản": ["VHM.VN", "VIC.VN", "VRE.VN", "NVL.VN", "DIG.VN", "DXG.VN", "KDH.VN", "NLG.VN", "PDR.VN", "CEO.VN", "HDG.VN", "DXS.VN", "CRE.VN", "SZC.VN", "KBC.VN", "IDC.VN"],
    "📈 Chứng khoán": ["SSI.VN", "VND.VN", "VCI.VN", "HCM.VN", "SHS.VN", "MBS.VN", "VIX.VN", "FTS.VN", "BSI.VN", "CTS.VN", "AGR.VN"],
    "🏭 Thép & Vật liệu": ["HPG.VN", "HSG.VN", "NKG.VN", "HT1.VN", "BCC.VN", "POM.VN", "SMC.VN", "VGC.VN", "BMP.VN"],
    "🛒 Bán lẻ & Tiêu dùng": ["MWG.VN", "PNJ.VN", "FRT.VN", "DGW.VN", "PET.VN", "HAX.VN", "VNM.VN", "MSN.VN", "SAB.VN"],
    "💻 Công nghệ & Viễn thông": ["FPT.VN", "CMG.VN", "ELC.VN", "ITD.VN", "VGI.VN", "CTR.VN", "FOX.VN"],
    "⚡ Năng lượng": ["GAS.VN", "POW.VN", "PLX.VN", "PVD.VN", "PVS.VN", "NT2.VN", "GEG.VN", "PC1.VN", "REE.VN"],
    "📦 Cảng & Logistics": ["GMD.VN", "HAH.VN", "VSC.VN", "SGP.VN", "MVN.VN", "VOS.VN", "PVT.VN"],
    "🐟 Nông Lâm Thủy Sản": ["VHC.VN", "ANV.VN", "FMC.VN", "DBC.VN", "HAG.VN", "BAF.VN", "LTG.VN", "TAR.VN", "PAN.VN", "IDI.VN"],
    "🏗️ Xây dựng & Đầu tư công": ["VCG.VN", "HHV.VN", "C4G.VN", "LCG.VN", "FCN.VN", "HBC.VN", "CTD.VN", "HUT.VN"],
    "💊 Y tế & Hóa chất": ["DGC.VN", "DPM.VN", "DCM.VN", "CSV.VN", "DHG.VN", "IMP.VN", "DBD.VN"]
}

TICKER_TO_SECTOR = {}
for sector_name, tickers in SECTORS.items():
    clean_name = sector_name.split(" ", 1)[1]
    for t in tickers: TICKER_TO_SECTOR[t] = clean_name

@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        if "FIREBASE_JSON" in st.secrets:
            key_dict = json.loads(st.secrets["FIREBASE_JSON"])
            cred = credentials.Certificate(key_dict)
        else: cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

try: db = init_firebase()
except Exception as e: st.error("Lỗi Database"); st.stop()

# ==========================================
# SIDEBAR
# ==========================================
st.sidebar.markdown("### ⚙️ Quản lý Danh mục")
doc_ref = db.collection("system_config").document("watchlist")
doc = doc_ref.get()
current_watchlist = doc.to_dict().get("tickers", []) if doc.exists else ["FPT.VN"]

if st.sidebar.button("🌐 THÊM TOÀN THỊ TRƯỜNG (TOP 100)"):
    doc_ref.set({"tickers": list(TICKER_TO_SECTOR.keys())})
    st.rerun()

new_ticker = st.sidebar.text_input("Thêm mã đơn lẻ:")
if st.sidebar.button("➕ Thêm Mã") and new_ticker:
    if new_ticker.upper() not in current_watchlist:
        current_watchlist.append(new_ticker.upper())
        doc_ref.set({"tickers": current_watchlist})
        st.rerun()

st.sidebar.markdown(f"**Đang rà soát: {len(current_watchlist)} mã**")
if st.sidebar.button("🗑️ XÓA TOÀN BỘ DANH SÁCH"):
    doc_ref.set({"tickers": []})
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎛️ Bảng Điều Khiển Wyckoff")
param_ref = db.collection("system_config").document("wyckoff_params")
param_doc = param_ref.get()
current_params = param_doc.to_dict() if param_doc.exists else {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}

with st.sidebar.form("param_form"):
    new_ma = st.number_input("Chu kỳ MA", 10, 50, int(current_params.get("vol_ma_period", 20)))
    new_sc_mult = st.slider("Hệ số Vol Selling Climax", 1.5, 5.0, float(current_params.get("sc_vol_multiplier", 2.5)), 0.1)
    new_spring_vol = st.slider("Ngưỡng Vol Cạn cung", 0.1, 1.0, float(current_params.get("spring_vol_ratio", 0.5)), 0.1)
    new_tolerance = st.slider("Độ lệch đáy (%)", 1.0, 15.0, float((current_params.get("spring_price_tolerance", 1.05) - 1) * 100), 0.5)
    if st.form_submit_button("Lưu Cấu Hình"):
        current_params = {"vol_ma_period": new_ma, "sc_vol_multiplier": new_sc_mult, "spring_vol_ratio": new_spring_vol, "spring_price_tolerance": 1 + (new_tolerance / 100)}
        param_ref.set(current_params)
        st.rerun()

# ==========================================
# MAIN UI
# ==========================================
st.title("Trạm Radar Tín Hiệu Định Lượng PRO")

@st.cache_data(ttl=60)
def load_signals():
    docs = db.collection('wyckoff_signals').order_by('Date_Detected', direction=firestore.Query.DESCENDING).limit(100).stream()
    return pd.DataFrame([doc.to_dict() for doc in docs])

df_signals = load_signals()
col1, col2, col3 = st.columns(3)
col1.metric("Tổng Tín Hiệu", len(df_signals))
col2.metric("🟢 Khuyến Nghị MUA", len(df_signals[df_signals['Signal_Type'].str.contains("Mua", na=False)]) if not df_signals.empty else 0)
col3.metric("🔴 Cảnh Báo BÁN", len(df_signals[df_signals['Signal_Type'].str.contains("Bán", na=False)]) if not df_signals.empty else 0)

@st.cache_data
def convert_df_to_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

tab_radar, tab_heatmap, tab_scan_chart = st.tabs(["📡 Radar Đa Khung", "🗺️ Bản Đồ Dòng Tiền", "🚀 Quét & Biểu Đồ VSA"])

with tab_radar:
    if not df_signals.empty:
        df_display = df_signals.copy()
        df_display['Nhóm Ngành'] = df_display['Ticker'].apply(lambda x: TICKER_TO_SECTOR.get(x, "Khác"))
        st.dataframe(df_display[['Date_Detected', 'Ticker', 'Nhóm Ngành', 'Signal_Type', 'Price', 'TR_Top', 'TR_Bottom', 'RS_Score', 'Weekly_Trend', 'VSA_Tags']], use_container_width=True, hide_index=True)
    else: st.info("Chưa có tín hiệu.")

with tab_heatmap:
    if not df_signals.empty:
        df_buy = df_signals[df_signals['Signal_Type'].str.contains("Mua", na=False, case=False)].copy()
        if not df_buy.empty:
            df_buy['Sector'] = df_buy['Ticker'].apply(lambda x: TICKER_TO_SECTOR.get(x, "Khác"))
            fig_tree = px.treemap(df_buy, path=[px.Constant("Thị Trường"), 'Sector', 'Ticker'], values='RS_Score', color='RS_Score', color_continuous_scale='RdYlGn')
            st.plotly_chart(fig_tree, use_container_width=True)

with tab_scan_chart:
    if st.button("▶️ KHỞI CHẠY MÁY QUÉT NGAY LẬP TỨC", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        signals_found = 0
        error_count = 0
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        vsa_engine = WyckoffVSASignal(current_params)
        
        import time
        for i, ticker in enumerate(current_watchlist):
            status_text.text(f"Đang phân tích: {ticker}...")
            try:
                fetcher = QuantDataFetcher(ticker)
                df = fetcher.fetch_daily_data(start_date, end_date)
                
                if df is None or df.empty:
                    error_count += 1
                    continue
                
                current_price = float(df['Close'].iloc[-1])
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
                
                if tr_top is not None:
                    signal_type = vsa_engine.detect_advanced_signals(df, current_price, tr_top, tr_bottom)
                    if signal_type:
                        db.collection('wyckoff_signals').add({
                            "Date_Detected": df.index[-1].strftime('%Y-%m-%d'), "Ticker": ticker, "Price": current_price,
                            "Signal_Type": signal_type, "TR_Top": tr_top, "TR_Bottom": tr_bottom, 
                            "RS_Score": round(((current_price - float(df['Close'].iloc[-60])) / float(df['Close'].iloc[-60])) * 100, 2),
                            "Weekly_Trend": vsa_engine.check_weekly_trend(df), "VSA_Tags": vsa_engine.get_vsa_tags(df),
                            "Timestamp": firestore.SERVER_TIMESTAMP
                        })
                        signals_found += 1
            except: error_count += 1
            progress_bar.progress((i + 1) / len(current_watchlist))
            
        status_text.success(f"✅ Quét xong! Tìm thấy {signals_found} tín hiệu. Bị mù dữ liệu: {error_count} mã.")
        st.cache_data.clear() 

    st.markdown("### 📈 Biểu Đồ Cấu Trúc Wyckoff Trực Quan")
    selected_chart_ticker = st.selectbox("Chọn mã cổ phiếu xem Chart:", current_watchlist)
    
    if selected_chart_ticker:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        
        with st.spinner("Đang tải dữ liệu biểu đồ..."):
            fetcher = QuantDataFetcher(selected_chart_ticker)
            df_chart = fetcher.fetch_daily_data(start_date, end_date)
            
            if df_chart is not None and not df_chart.empty:
                vsa_engine = WyckoffVSASignal(current_params)
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df_chart)
                
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
                fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name="Giá"), row=1, col=1)
                
                if tr_top and tr_bottom:
                    fig.add_hline(y=tr_top, line_dash="dash", line_color="green", annotation_text="Kháng cự", row=1, col=1)
                    fig.add_hline(y=tr_bottom, line_dash="dash", line_color="red", annotation_text="Hỗ trợ", row=1, col=1)
                
                colors = ['red' if row['Close'] < row['Open'] else 'green' for index, row in df_chart.iterrows()]
                fig.add_trace(go.Bar(x=df_chart.index, y=df_chart['Volume'], marker_color=colors, name="Khối lượng"), row=2, col=1)
                
                # Nối liền biểu đồ không bị đứt đoạn cuối tuần
                fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
                fig.update_layout(title=f"VSA & Dòng tiền: {selected_chart_ticker}", yaxis_title="Giá", xaxis_rangeslider_visible=False, height=600, template="plotly_white")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error(f"❌ KHÔNG CÓ DỮ LIỆU. Thư viện vnstock và yfinance đều bị chặn lấy mã {selected_chart_ticker}.")
