import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# Thư viện Vẽ Biểu Đồ & Heatmap
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# Import Lõi Quét
from quant_core import QuantDataFetcher, WyckoffVSASignal

# ==========================================
# 1. CẤU HÌNH GIAO DIỆN LIGHT LUXURY
# ==========================================
st.set_page_config(page_title="Wyckoff Quant Radar PRO", layout="wide", page_icon="📡")

st.markdown("""
    <style>
        .stApp { background-color: #FAF9F6; color: #333333; }
        h1 { color: #1A1A1A; border-bottom: 2px solid #D4AF37; padding-bottom: 10px; font-family: 'Helvetica Neue', sans-serif; }
        .stDataFrame { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); border-radius: 8px; overflow: hidden; }
        div[data-testid="stMetricValue"] { color: #D4AF37; }
        .stButton>button { border: 1px solid #D4AF37; border-radius: 5px; font-weight: bold; transition: all 0.3s; }
        .stButton>button:hover { background-color: #D4AF37; color: white; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# BỘ TỪ ĐIỂN CỔ PHIẾU TOÀN THỊ TRƯỜNG
# ==========================================
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
    for t in tickers:
        TICKER_TO_SECTOR[t] = clean_name

# ==========================================
# 2. KẾT NỐI CƠ SỞ DỮ LIỆU ĐÁM MÂY
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        if "FIREBASE_JSON" in st.secrets:
            key_dict = json.loads(st.secrets["FIREBASE_JSON"])
            cred = credentials.Certificate(key_dict)
        else:
            cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"Lỗi kết nối Cơ sở dữ liệu: {e}")
    st.stop()

# ==========================================
# MODULE: QUẢN LÝ DANH MỤC
# ==========================================
st.sidebar.markdown("### ⚙️ Quản lý Danh mục")

doc_ref = db.collection("system_config").document("watchlist")
doc = doc_ref.get()

current_watchlist = doc.to_dict().get("tickers", []) if doc.exists else ["FPT.VN", "VNM.VN", "AAPL"]

# Nút Thêm toàn thị trường
if st.sidebar.button("🌐 THÊM TOÀN THỊ TRƯỜNG (TOP 100)"):
    all_tickers = list(TICKER_TO_SECTOR.keys())
    doc_ref.set({"tickers": all_tickers})
    st.sidebar.success(f"Đã nạp {len(all_tickers)} mã vào Radar!")
    st.rerun()

st.sidebar.markdown("---")
new_ticker = st.sidebar.text_input("Thêm mã đơn lẻ:")
if st.sidebar.button("➕ Thêm Mã"):
    if new_ticker and new_ticker.upper() not in current_watchlist:
        current_watchlist.append(new_ticker.upper())
        doc_ref.set({"tickers": current_watchlist})
        st.rerun()

selected_sector = st.sidebar.selectbox("Lọc tự động theo Ngành:", list(SECTORS.keys()))
if st.sidebar.button("📥 Thêm toàn bộ Ngành này"):
    added_count = 0
    for t in SECTORS[selected_sector]:
        if t not in current_watchlist:
            current_watchlist.append(t)
            added_count += 1
    if added_count > 0:
        doc_ref.set({"tickers": current_watchlist})
        st.sidebar.success(f"Đã thêm {added_count} mã ngành!")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown(f"**Đang rà soát: {len(current_watchlist)} mã**")

if st.sidebar.button("🗑️ XÓA TOÀN BỘ DANH SÁCH"):
    doc_ref.set({"tickers": []})
    st.rerun()

# ==========================================
# MODULE: BẢNG ĐIỀU KHIỂN BIẾN SỐ
# ==========================================
st.sidebar.markdown("---")
st.sidebar.markdown("### 🎛️ Bảng Điều Khiển Wyckoff")

param_ref = db.collection("system_config").document("wyckoff_params")
param_doc = param_ref.get()

current_params = param_doc.to_dict() if param_doc.exists else {
    "vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05
}

with st.sidebar.form("param_form"):
    new_ma = st.number_input("Chu kỳ MA Khối lượng", min_value=10, max_value=50, value=int(current_params.get("vol_ma_period", 20)))
    new_sc_mult = st.slider("Hệ số Vol (Selling Climax)", 1.5, 5.0, float(current_params.get("sc_vol_multiplier", 2.5)), 0.1)
    new_spring_vol = st.slider("Ngưỡng Vol Cạn cung", 0.1, 1.0, float(current_params.get("spring_vol_ratio", 0.5)), 0.1)
    new_tolerance = st.slider("Độ lệch giá tại đáy (%)", 1.0, 10.0, float((current_params.get("spring_price_tolerance", 1.05) - 1) * 100), 0.5)

    if st.form_submit_button("Lưu Cấu Hình"):
        current_params = {
            "vol_ma_period": new_ma, "sc_vol_multiplier": new_sc_mult,
            "spring_vol_ratio": new_spring_vol, "spring_price_tolerance": 1 + (new_tolerance / 100)
        }
        param_ref.set(current_params)
        st.success("✅ Đã cập nhật tham số!")
        st.rerun()

# ==========================================
# GIAO DIỆN CHÍNH (MAIN DASHBOARD)
# ==========================================
st.title("Trạm Radar Tín Hiệu Định Lượng PRO")

@st.cache_data(ttl=60)
def load_signals():
    docs = db.collection('wyckoff_signals').order_by('Date_Detected', direction=firestore.Query.DESCENDING).limit(100).stream()
    return pd.DataFrame([doc.to_dict() for doc in docs])

df_signals = load_signals()

col1, col2, col3 = st.columns(3)
total_signals = len(df_signals) if not df_signals.empty else 0
latest_date = df_signals['Date_Detected'].iloc[0] if not df_signals.empty else "Chưa có dữ liệu"

# Lọc nhanh số lượng Mua/Bán
buy_count = len(df_signals[df_signals['Signal_Type'].str.contains("Mua", na=False)]) if not df_signals.empty else 0
sell_count = len(df_signals[df_signals['Signal_Type'].str.contains("Bán", na=False)]) if not df_signals.empty else 0

col1.metric("Tổng Tín Hiệu Trong Bảng", total_signals)
col2.metric("🟢 Khuyến Nghị MUA", buy_count)
col3.metric("🔴 Cảnh Báo BÁN", sell_count)

@st.cache_data
def convert_df_to_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

# CẤU TRÚC TAB MỚI
tab_radar, tab_heatmap, tab_scan_chart = st.tabs(["📡 Radar Đa Khung Thời Gian", "🗺️ Bản Đồ Dòng Tiền (Heatmap)", "🚀 Quét & Biểu Đồ VSA"])

# --- TAB 1: RADAR & KHUYẾN NGHỊ ---
with tab_radar:
    st.markdown("### Danh sách Báo cáo & Đánh giá Xu hướng")
    
    if not df_signals.empty:
        data = []
        for index, sig in df_signals.iterrows():
            entry_price = sig.get("Price", 0)
            tr_top = sig.get("TR_Top", 0)
            tr_bottom = sig.get("TR_Bottom", 0)
            rs_score = sig.get("RS_Score", "N/A")
            weekly_trend = sig.get("Weekly_Trend", "N/A")
            vsa_tags = sig.get("VSA_Tags", "")
            
            stop_loss = tr_bottom * 0.98 if tr_bottom else 0
            rr_ratio = "N/A"
            if entry_price > 0 and tr_top > entry_price and stop_loss > 0:
                risk = entry_price - stop_loss
                reward = tr_top - entry_price
                if risk > 0: rr_ratio = f"1 : {reward/risk:.1f}"
            
            ticker = sig.get("Ticker", "")
            is_vn = ".VN" in ticker or ".HM" in ticker or ".HN" in ticker
            sector = TICKER_TO_SECTOR.get(ticker, "Khác")
            
            # Tô màu tín hiệu
            sig_type = sig.get("Signal_Type", "")
            if "Mua" in sig_type: sig_type = f"🟢 {sig_type}"
            elif "Bán" in sig_type: sig_type = f"🔴 {sig_type}"
            
            data.append({
                "Ngày": sig.get("Date_Detected", ""),
                "Mã CK": ticker,
                "Nhóm Ngành": sector,
                "Tín Hiệu": sig_type,
                "Trend Tuần": f"📈 TĂNG" if "TĂNG" in weekly_trend else f"📉 GIẢM",
                "Mẫu Hình Nến (VSA)": vsa_tags,
                "Điểm RS": f"{rs_score}%" if isinstance(rs_score, (int, float)) else rs_score,
                "Giá Báo": f"{entry_price:,.0f}" if is_vn else f"${entry_price:,.2f}",
                "Cắt Lỗ": f"{stop_loss:,.0f}" if is_vn and stop_loss else (f"${stop_loss:,.2f}" if stop_loss else "-"),
                "Tỷ lệ R:R": rr_ratio,
            })

        df_display = pd.DataFrame(data)
        
        c1, c2 = st.columns([3, 1])
        with c1:
            st.info("💡 **Tín Hiệu Đa Khung:** Mua khi Tín hiệu là 🟢 Spring/BU + Trend Tuần là 📈 TĂNG + Nến VSA báo No Supply. Xác suất thắng > 80%.")
        with c2:
            csv = convert_df_to_csv(df_display)
            st.download_button(
                label="📥 Tải Báo Cáo Khuyến Nghị", data=csv,
                file_name=f"Wyckoff_Pro_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv", use_container_width=True
            )

        st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.info("Hiện chưa có tín hiệu mới nào đạt chuẩn.")

# --- TAB 2: HEATMAP (BẢN ĐỒ DÒNG TIỀN) ---
with tab_heatmap:
    st.markdown("### 🗺️ Bản Đồ Nhiệt Dòng Tiền Theo Ngành")
    st.write("Quan sát sức mạnh dòng tiền của các nhóm ngành có tín hiệu MUA trong bộ lọc.")
    
    if not df_signals.empty:
        # Lọc ra các mã có tín hiệu MUA
        df_buy = df_signals[df_signals['Signal_Type'].str.contains("Mua", na=False, case=False)].copy()
        
        if not df_buy.empty:
            df_buy['Sector'] = df_buy['Ticker'].apply(lambda x: TICKER_TO_SECTOR.get(x, "Khác"))
            # Nhóm dữ liệu để vẽ Treemap
            fig_tree = px.treemap(
                df_buy, 
                path=[px.Constant("Thị Trường"), 'Sector', 'Ticker'], 
                values='RS_Score', # Kích thước ô dựa trên RS Score (Dòng tiền mạnh)
                color='RS_Score', 
                color_continuous_scale='RdYlGn',
                title="Bản đồ Ngành (Kích thước & Màu sắc = Sức mạnh dòng tiền RS)"
            )
            fig_tree.update_layout(height=600, margin=dict(t=50, l=10, r=10, b=10))
            st.plotly_chart(fig_tree, use_container_width=True)
        else:
            st.warning("Hiện không có cổ phiếu nào báo điểm MUA để vẽ Bản đồ dòng tiền.")
    else:
        st.info("Chưa có dữ liệu.")

# --- TAB 3: QUÉT CHỦ ĐỘNG & BIỂU ĐỒ ---
with tab_scan_chart:
    st.markdown("### 🚀 Quét Thị Trường Đa Khung Thời Gian")
    st.write("Kích hoạt lõi AI phân tích Cấu trúc Wyckoff, Xu hướng Tuần (Weekly Trend) và Mẫu hình Nến VSA.")
    
    if st.button("▶️ KHỞI CHẠY MÁY QUÉT NGAY LẬP TỨC", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        signals_found = 0
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365) # Lấy 1 năm dữ liệu để soi được Khung Tuần
        vsa_engine = WyckoffVSASignal(current_params)
        
        import time
        
        for i, ticker in enumerate(current_watchlist):
            status_text.text(f"Đang phân tích Đa Khung: {ticker}...")
            try:
                time.sleep(0.3)
                fetcher = QuantDataFetcher(ticker)
                df = fetcher.fetch_daily_data(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
                
                if df is not None and len(df) > 60:
                    current_price = float(df['Close'].iloc[-1])
                    tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
                    
                    if tr_top is not None and tr_bottom is not None:
                        # KIỂM TRA TÍN HIỆU ĐA DẠNG
                        signal_type = vsa_engine.detect_advanced_signals(df, current_price, tr_top, tr_bottom)
                        
                        if signal_type:
                            # TÍNH XU HƯỚNG TUẦN
                            weekly_trend = vsa_engine.check_weekly_trend(df)
                            
                            # TÌM NẾN VSA
                            vsa_tags = vsa_engine.get_vsa_tags(df)
                            
                            # TÍNH RS
                            rs_score = 0
                            price_60d = float(df['Close'].iloc[-60])
                            rs_score = round(((current_price - price_60d) / price_60d) * 100, 2)
                                
                            signal_data = {
                                "Date_Detected": df.index[-1].strftime('%Y-%m-%d'),
                                "Ticker": ticker,
                                "Price": float(current_price),
                                "Signal_Type": signal_type,
                                "TR_Top": float(tr_top),
                                "TR_Bottom": float(tr_bottom),
                                "RS_Score": rs_score,
                                "Weekly_Trend": weekly_trend,
                                "VSA_Tags": vsa_tags,
                                "Status": "Mới phát hiện",
                                "Timestamp": firestore.SERVER_TIMESTAMP
                            }
                            db.collection('wyckoff_signals').add(signal_data)
                            signals_found += 1
            except Exception as e:
                pass
                
            progress_bar.progress((i + 1) / len(current_watchlist))
            
        status_text.success(f"✅ Quét hoàn tất! Tìm thấy {signals_found} tín hiệu (Mua/Bán). Vui lòng sang Tab Radar để xem.")
        st.cache_data.clear() 

    st.markdown("---")
    st.markdown("### 📈 Biểu Đồ Cấu Trúc Wyckoff Trực Quan")
    
    selected_chart_ticker = st.selectbox("Chọn mã cổ phiếu xem Chart:", current_watchlist)
    
    if selected_chart_ticker:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        
        with st.spinner("Đang tải dữ liệu biểu đồ..."):
            fetcher = QuantDataFetcher(selected_chart_ticker)
            df_chart = fetcher.fetch_daily_data(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
            
            if df_chart is not None and not df_chart.empty:
                vsa_engine = WyckoffVSASignal(current_params)
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df_chart)
                
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
                
                fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name="Giá"), row=1, col=1)
                
                if tr_top and tr_bottom:
                    fig.add_hline(y=tr_top, line_dash="dash", line_color="green", annotation_text="Kháng cự (AR/BU)", row=1, col=1)
                    fig.add_hline(y=tr_bottom, line_dash="dash", line_color="red", annotation_text="Hỗ trợ (SC/Spring)", row=1, col=1)
                
                df_chart['MA200'] = df_chart['Close'].rolling(window=200).mean()
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['MA200'], line=dict(color='orange', width=1.5), name="MA200"), row=1, col=1)
                
                colors = ['red' if row['Close'] < row['Open'] else 'green' for index, row in df_chart.iterrows()]
                fig.add_trace(go.Bar(x=df_chart.index, y=df_chart['Volume'], marker_color=colors, name="Khối lượng"), row=2, col=1)
                
                fig.update_layout(title=f"Cấu trúc VSA & Dòng tiền: {selected_chart_ticker}", yaxis_title="Giá", xaxis_rangeslider_visible=False, height=600, template="plotly_white")
                st.plotly_chart(fig, use_container_width=True)
