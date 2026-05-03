import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
import requests

from quant_core import QuantDataFetcher, WyckoffVSASignal

# ==========================================
# 1. CẤU HÌNH GIAO DIỆN
# ==========================================
st.set_page_config(page_title="Wyckoff Quant Radar PRO", layout="wide", page_icon="📡")
st.markdown("""<style>.stApp { background-color: #FAF9F6; color: #333333; } h1 { color: #1A1A1A; border-bottom: 2px solid #D4AF37; padding-bottom: 10px; } .stDataFrame { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); border-radius: 8px; } div[data-testid="stMetricValue"] { color: #D4AF37; }</style>""", unsafe_allow_html=True)

SECTORS = {
    "🏦 Ngân hàng": ["VCB", "BID", "CTG", "TCB", "VPB", "MBB", "ACB", "STB", "HDB", "VIB", "TPB", "SHB", "EIB", "MSB", "OCB", "LPB"],
    "🏢 Bất động sản": ["VHM", "VIC", "VRE", "NVL", "DIG", "DXG", "KDH", "NLG", "PDR", "CEO", "HDG", "DXS", "CRE", "SZC", "KBC", "IDC"],
    "📈 Chứng khoán": ["SSI", "VND", "VCI", "HCM", "SHS", "MBS", "VIX", "FTS", "BSI", "CTS", "AGR"],
    "🏭 Thép & Vật liệu": ["HPG", "HSG", "NKG", "HT1", "BCC", "POM", "SMC", "VGC", "BMP"],
    "🛒 Bán lẻ & Tiêu dùng": ["MWG", "PNJ", "FRT", "DGW", "PET", "HAX", "VNM", "MSN", "SAB"],
    "💻 Công nghệ & Viễn thông": ["FPT", "CMG", "ELC", "ITD", "VGI", "CTR", "FOX"],
    "⚡ Năng lượng": ["GAS", "POW", "PLX", "PVD", "PVS", "NT2", "GEG", "PC1", "REE"],
    "📦 Cảng & Logistics": ["GMD", "HAH", "VSC", "SGP", "MVN", "VOS", "PVT"],
    "🐟 Nông Lâm Thủy Sản": ["VHC", "ANV", "FMC", "DBC", "HAG", "BAF", "LTG", "TAR", "PAN", "IDI"],
    "🏗️ Xây dựng & Đầu tư công": ["VCG", "HHV", "C4G", "LCG", "FCN", "HBC", "CTD", "HUT"],
    "💊 Y tế & Hóa chất": ["DGC", "DPM", "DCM", "CSV", "DHG", "IMP", "DBD"]
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
current_watchlist = doc.to_dict().get("tickers", []) if doc.exists else ["FPT"]

if st.sidebar.button("🌐 THÊM TOÀN THỊ TRƯỜNG (TOP 100)"):
    doc_ref.set({"tickers": list(TICKER_TO_SECTOR.keys())})
    st.rerun()

new_ticker = st.sidebar.text_input("Thêm mã đơn lẻ:")
if st.sidebar.button("➕ Thêm Mã") and new_ticker:
    clean_ticker = new_ticker.upper().replace(".VN", "").strip()
    if clean_ticker not in current_watchlist:
        current_watchlist.append(clean_ticker)
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
st.title("Trạm Radar Tín Hiệu Định Lượng PRO 🌟")

@st.cache_data(ttl=60)
def load_signals():
    docs = db.collection('wyckoff_signals').order_by('Date_Detected', direction=firestore.Query.DESCENDING).limit(100).stream()
    df = pd.DataFrame([doc.to_dict() for doc in docs])
    
    # 🔥 BƯỚC VÁ LỖI QUAN TRỌNG: Tự động điền các cột thiếu cho dữ liệu cũ để chống crash
    if not df.empty:
        expected_cols = {
            'Rating_Score': 50, 'RS_Score': 0, 'POC_Level': 0, 'Trailing_Stop': 0, 
            'Take_Profit_1': 0, 'Take_Profit_2': 0, 'Weekly_Trend': 'N/A', 'VSA_Tags': ''
        }
        for col, default_val in expected_cols.items():
            if col not in df.columns:
                df[col] = default_val
    return df

df_signals = load_signals()

col1, col2, col3 = st.columns(3)
col1.metric("Tổng Tín Hiệu", len(df_signals))
col2.metric("🟢 Khuyến Nghị MUA", len(df_signals[df_signals['Signal_Type'].str.contains("Mua", na=False)]) if not df_signals.empty else 0)
col3.metric("🔴 Cảnh Báo BÁN", len(df_signals[df_signals['Signal_Type'].str.contains("Bán", na=False)]) if not df_signals.empty else 0)

@st.cache_data
def convert_df_to_csv(df): return df.to_csv(index=False).encode('utf-8-sig')

# THÊM TAB QUẢN TRỊ VỐN VÀ NHẬT KÝ
tab_radar, tab_heatmap, tab_scan_chart, tab_capital, tab_alerts = st.tabs(["📡 Radar Chấm Điểm AI", "🗺️ Bản Đồ Dòng Tiền", "🚀 Quét & Biểu Đồ POC", "🧮 Quản Trị Vốn & Nhật Ký", "🤖 Bot Telegram"])

with tab_radar:
    st.markdown("### Danh sách Báo cáo & Chấm Điểm Toàn Diện")
    if not df_signals.empty:
        df_display = df_signals.copy()
        
        show_super_only = st.checkbox("🔥 Chỉ Lọc Các Siêu Cổ Phiếu (Rating >= 80)")
        if show_super_only:
            df_display = df_display[df_display['Rating_Score'] >= 80]
            
        if not df_display.empty:
            df_display['Nhóm Ngành'] = df_display['Ticker'].apply(lambda x: TICKER_TO_SECTOR.get(x.replace(".VN", ""), "Khác"))
            
            formatted_data = []
            for _, sig in df_display.iterrows():
                ticker = sig.get("Ticker", "")
                entry = sig.get("Price", 0)
                sl = sig.get("Trailing_Stop", entry * 0.98) 
                rating = sig.get("Rating_Score", 50)
                tp1 = sig.get("Take_Profit_1", 0)
                tp2 = sig.get("Take_Profit_2", 0)
                
                sig_type = sig.get("Signal_Type", "")
                if "Mua" in sig_type: sig_type = f"🟢 {sig_type}"
                elif "Bán" in sig_type: sig_type = f"🔴 {sig_type}"
                
                formatted_data.append({
                    "Ngày": sig.get("Date_Detected", ""),
                    "Mã CK": f"🌟 {ticker}" if rating >= 80 else ticker,
                    "Ngành": sig.get("Nhóm Ngành", ""),
                    "Rating /100": f"{rating} đ",
                    "Tín Hiệu": sig_type,
                    "Cắt Lỗ ATR": f"{sl:,.0f}" if entry > 1000 else f"${sl:,.2f}",
                    "Chốt lời (TP1 - TP2)": f"{tp1:,.0f} - {tp2:,.0f}" if entry > 1000 else f"${tp1:,.2f} - ${tp2:,.2f}",
                    "Hỗ trợ Hút tiền (POC)": f"{sig.get('POC_Level', 0):,.0f}" if entry > 1000 and sig.get('POC_Level') else "-",
                    "Trend Tuần": f"📈" if "TĂNG" in str(sig.get("Weekly_Trend", "")) else f"📉",
                    "VSA_Tags": sig.get("VSA_Tags", "")
                })

            df_show = pd.DataFrame(formatted_data)
            c1, c2 = st.columns([4, 1])
            with c1: st.info("💡 **Rating /100:** AI đánh giá tổng hợp điểm sức mạnh. Các mã trên 80 điểm (🌟) là các mã có dòng tiền, xu hướng và VSA đẹp nhất. Dùng **Cắt Lỗ ATR** để gồng lãi.")
            with c2: st.download_button("📥 Tải Báo Cáo", data=convert_df_to_csv(df_show), file_name=f"Wyckoff_Pro_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
            st.dataframe(df_show, use_container_width=True, hide_index=True)
        else:
            st.info("Không có mã nào đạt chuẩn Siêu cổ phiếu lúc này.")
    else: st.info("Chưa có tín hiệu.")

with tab_heatmap:
    st.markdown("### 🗺️ Bản Đồ Nhiệt Dòng Tiền (Theo Rating Điểm Số)")
    if not df_signals.empty:
        df_buy = df_signals[df_signals['Signal_Type'].str.contains("Mua", na=False, case=False)].copy()
        if not df_buy.empty:
            df_buy['Sector'] = df_buy['Ticker'].apply(lambda x: TICKER_TO_SECTOR.get(x.replace(".VN", ""), "Khác"))
            fig_tree = px.treemap(df_buy, path=[px.Constant("Thị Trường"), 'Sector', 'Ticker'], values='Rating_Score', color='RS_Score', color_continuous_scale='RdYlGn')
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
                        rs_score = round(((current_price - float(df['Close'].iloc[-60])) / float(df['Close'].iloc[-60])) * 100, 2)
                        weekly_trend = vsa_engine.check_weekly_trend(df)
                        vsa_tags = vsa_engine.get_vsa_tags(df)
                        atr_val = vsa_engine.calculate_atr(df)
                        poc_val = vsa_engine.calculate_poc(df, tr_bottom, tr_top)
                        trailing_stop = round(current_price - (1.5 * atr_val), 2) if atr_val else 0

                        tp1 = round(current_price + (tr_top - current_price) * 0.5, 2)
                        tp2 = round(tr_top, 2)

                        rating = 50
                        if rs_score > 0: rating += min(rs_score, 20)
                        if weekly_trend == "TĂNG (Uptrend)": rating += 15
                        if "No Supply" in vsa_tags or "Stopping Vol" in vsa_tags: rating += 15
                        rating = int(min(max(rating, 0), 100))

                        db.collection('wyckoff_signals').add({
                            "Date_Detected": df.index[-1].strftime('%Y-%m-%d'), "Ticker": ticker, "Price": current_price,
                            "Signal_Type": signal_type, "TR_Top": tr_top, "TR_Bottom": tr_bottom, "RS_Score": rs_score,
                            "Weekly_Trend": weekly_trend, "VSA_Tags": vsa_tags, "Rating_Score": rating, 
                            "Trailing_Stop": trailing_stop, "POC_Level": poc_val, 
                            "Take_Profit_1": tp1, "Take_Profit_2": tp2,
                            "Timestamp": firestore.SERVER_TIMESTAMP
                        })
                        signals_found += 1
            except: error_count += 1
            progress_bar.progress((i + 1) / len(current_watchlist))
            
        status_text.success(f"✅ Quét xong! Tìm thấy {signals_found} tín hiệu. Bỏ qua do rác/thanh khoản kém: {error_count} mã.")
        st.cache_data.clear() 

    st.markdown("### 📈 Biểu Đồ Volume Profile & POC")
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
                    poc_val = vsa_engine.calculate_poc(df_chart, tr_bottom, tr_top)
                    if poc_val:
                        fig.add_hline(y=poc_val, line_dash="dot", line_color="gold", annotation_text="POC (Lõi Dòng Tiền)", row=1, col=1)

                colors = ['red' if row['Close'] < row['Open'] else 'green' for index, row in df_chart.iterrows()]
                fig.add_trace(go.Bar(x=df_chart.index, y=df_chart['Volume'], marker_color=colors, name="Khối lượng"), row=2, col=1)
                fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
                fig.update_layout(title=f"VSA & Dòng tiền: {selected_chart_ticker}", yaxis_title="Giá", xaxis_rangeslider_visible=False, height=600, template="plotly_dark")
                st.plotly_chart(fig, use_container_width=True)

# TÍNH NĂNG MỚI: QUẢN TRỊ VỐN VÀ NHẬT KÝ
with tab_capital:
    st.markdown("### 🧮 Máy Tính Đi Lệnh Chuyên Nghiệp (Position Sizing)")
    st.info("Nhập số vốn để AI tính ra chính xác bạn nên mua bao nhiêu cổ phiếu để không bao giờ phá sản dù thị trường sập.")
    
    col_a, col_b = st.columns(2)
    with col_a:
        capital = st.number_input("Tổng vốn đầu tư (VND):", value=100000000, step=10000000)
        risk_pct = st.slider("Rủi ro tối đa cho phép / 1 Lệnh (%):", min_value=0.5, max_value=5.0, value=2.0, step=0.1)
    with col_b:
        entry_price = st.number_input("Giá Mua dự kiến:", value=50000, step=100)
        stop_loss = st.number_input("Giá Cắt Lỗ (SL):", value=47000, step=100)
        
    if entry_price > stop_loss > 0:
        risk_per_share = entry_price - stop_loss
        max_loss_amount = capital * (risk_pct / 100)
        shares_to_buy = int(max_loss_amount / risk_per_share)
        total_investment = shares_to_buy * entry_price
        
        st.success(f"🎯 **KHUYẾN NGHỊ:** Bạn nên mua tối đa **{shares_to_buy:,.0f} Cổ phiếu**.")
        st.write(f"- 💵 Cần giải ngân: **{total_investment:,.0f} VND** (Chiếm {(total_investment/capital)*100:.1f}% tài khoản)")
        st.write(f"- 🛡️ Nếu bị quét Cắt lỗ, bạn chỉ mất: **{max_loss_amount:,.0f} VND** (Đúng chuẩn {risk_pct}% rủi ro)")
    elif stop_loss >= entry_price:
        st.warning("⚠️ Giá Cắt Lỗ phải nhỏ hơn Giá Mua dự kiến.")

    st.markdown("---")
    st.markdown("### 📔 Sổ Nhật Ký Giao Dịch Đám Mây")
    with st.form("journal_form"):
        j_ticker = st.text_input("Mã Cổ Phiếu:")
        j_note = st.text_area("Ghi chú lý do Mua/Bán (Bài học):")
        if st.form_submit_button("Lưu Vào Nhật Ký"):
            if j_ticker:
                db.collection("trading_journal").add({
                    "Ticker": j_ticker.upper(), "Note": j_note,
                    "Date": datetime.now().strftime('%Y-%m-%d %H:%M')
                })
                st.success("✅ Đã lưu nhật ký thành công!")
                st.rerun()
                
    journal_docs = db.collection("trading_journal").order_by("Date", direction=firestore.Query.DESCENDING).limit(10).stream()
    for doc in journal_docs:
        item = doc.to_dict()
        st.write(f"📅 **{item.get('Date')} | {item.get('Ticker')}**: {item.get('Note')}")

with tab_alerts:
    st.markdown("### 🤖 Tích Hợp Bot Cảnh Báo Telegram")
    st.write("Hệ thống Lõi sẽ tự động gửi thông báo các mã Siêu cổ phiếu đạt chuẩn về điện thoại của bạn.")
    
    tele_ref = db.collection("system_config").document("telegram")
    tele_doc = tele_ref.get()
    tele_data = tele_doc.to_dict() if tele_doc.exists else {"bot_token": "", "chat_id": ""}
    
    with st.form("tele_form"):
        bot_token = st.text_input("Bot Token (Lấy từ @BotFather):", value=tele_data.get("bot_token", ""))
        chat_id = st.text_input("Chat ID của bạn (Lấy từ @userinfobot):", value=tele_data.get("chat_id", ""))
        
        if st.form_submit_button("Lưu Cấu Hình & Gửi Test"):
            tele_ref.set({"bot_token": bot_token, "chat_id": chat_id})
            if bot_token and chat_id:
                try:
                    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    res = requests.post(url, json={"chat_id": chat_id, "text": "✅ Trạm Radar Wyckoff đã kết nối thành công!"})
                    if res.status_code == 200: st.success("Đã gửi tin nhắn test thành công tới Telegram!")
                    else: st.error("Lỗi: Kiểm tra lại Token hoặc Chat ID.")
                except Exception as e: st.error(f"Lỗi mạng: {e}")
            st.rerun()
