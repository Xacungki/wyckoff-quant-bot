import streamlit as st
import pandas as pd
import yfinance as yf
import json
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

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
        .stButton>button { border: 1px solid #D4AF37; color: #1A1A1A; border-radius: 5px; font-weight: bold; }
        .stButton>button:hover { background-color: #D4AF37; color: white; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. KẾT NỐI FIRESTORE
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        try:
            if "FIREBASE_JSON" in st.secrets:
                key_dict = json.loads(st.secrets["FIREBASE_JSON"])
                cred = credentials.Certificate(key_dict)
            else:
                cred = credentials.Certificate("firebase_key.json")
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Lỗi kết nối Firebase: {e}")
    return firestore.client()

db = init_firebase()

# ==========================================
# 3. LÕI ĐỊNH LƯỢNG (QUANT CORE) - TÍCH HỢP TRỰC TIẾP
# ==========================================
class WyckoffEngine:
    def __init__(self, params):
        self.params = params

    def get_data(self, ticker):
        yf_ticker = ticker.replace(".VN", ".HM") 
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365) # Lấy data 1 năm
        df = yf.download(yf_ticker, start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), interval="1d", progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df

    def analyze(self, df):
        if len(df) < 60: return None
        
        ma_period = self.params.get("vol_ma_period", 20)
        sc_mult = self.params.get("sc_vol_multiplier", 2.5)
        vol_ratio = self.params.get("spring_vol_ratio", 0.5)
        tolerance = self.params.get("spring_price_tolerance", 1.05)

        # Tính MA và Volume MA
        df['Vol_MA'] = df['Volume'].rolling(window=ma_period).mean()
        df['MA200'] = df['Close'].rolling(window=200).mean() # BỘ LỌC XU HƯỚNG
        
        # Tìm Selling Climax
        df['Is_SC'] = (df['Volume'] > df['Vol_MA'] * sc_mult) & (df['Close'] < df['Open'])
        sc_candles = df.tail(60)[df.tail(60)['Is_SC'] == True]
        
        if sc_candles.empty: return None
            
        sc_index = df.index.get_loc(sc_candles['Volume'].idxmax())
        tr_bottom = df['Low'].iloc[sc_index:min(sc_index+4, len(df))].min()
        tr_top = df['High'].iloc[sc_index+1:min(sc_index+21, len(df))].max() if sc_index + 20 < len(df) else df['High'].iloc[sc_index+1:].max()
        
        current_price = float(df['Close'].iloc[-1])
        latest_vol = float(df['Volume'].iloc[-1])
        latest_vol_ma = float(df['Vol_MA'].iloc[-1])

        # Đánh giá Cạn cung (Spring)
        is_near_support = current_price <= (tr_bottom * tolerance)
        is_low_volume = latest_vol < (latest_vol_ma * vol_ratio)
        
        if is_near_support and is_low_volume:
            # Đánh giá rủi ro dựa trên MA200
            ma200_val = df['MA200'].iloc[-1]
            risk_level = "Thấp (Nằm trên MA200)" if pd.notna(ma200_val) and current_price > ma200_val else "Cao (Bắt dao rơi)"
            
            return {
                "TR_Top": float(tr_top),
                "TR_Bottom": float(tr_bottom),
                "Risk_Level": risk_level,
                "Price": current_price
            }
        return None

# ==========================================
# 4. HÀM VẼ BIỂU ĐỒ (PLOTLY)
# ==========================================
def plot_wyckoff_chart(df, ticker, tr_top, tr_bottom):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    
    # Nến
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="Giá"), row=1, col=1)
    
    # MA200
    if 'MA200' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['MA200'], line=dict(color='orange', width=1.5), name="MA200"), row=1, col=1)

    # Khung giá (Trading Range)
    fig.add_hline(y=tr_top, line_dash="dash", line_color="green", annotation_text="Kháng cự (AR)", row=1, col=1)
    fig.add_hline(y=tr_bottom, line_dash="dash", line_color="red", annotation_text="Hỗ trợ (SC/Spring)", row=1, col=1)

    # Volume
    colors = ['red' if row['Close'] < row['Open'] else 'green' for index, row in df.iterrows()]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name="Khối lượng"), row=2, col=1)
    
    if 'Vol_MA' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['Vol_MA'], line=dict(color='blue', width=1), name="Vol MA"), row=2, col=1)

    fig.update_layout(title=f"Cấu trúc Wyckoff: {ticker}", yaxis_title="Giá", xaxis_rangeslider_visible=False, height=500, template="plotly_white")
    return fig

# ==========================================
# 5. SIDEBAR: DANH MỤC & THÔNG SỐ
# ==========================================
st.sidebar.markdown("### ⚙️ Cấu Hình Hệ Thống")

# Quản lý Watchlist
doc_ref = db.collection("system_config").document("watchlist")
doc = doc_ref.get()
watchlist = doc.to_dict().get("tickers", ["FPT.VN", "VNM.VN", "AAPL"]) if doc.exists else ["FPT.VN", "VNM.VN", "AAPL"]

new_ticker = st.sidebar.text_input("Thêm mã CK:")
if st.sidebar.button("➕ Thêm"):
    if new_ticker and new_ticker.upper() not in watchlist:
        watchlist.append(new_ticker.upper())
        doc_ref.set({"tickers": watchlist})
        st.rerun()

with st.sidebar.expander("Danh sách đang theo dõi", expanded=False):
    for ticker in watchlist:
        c1, c2 = st.columns([3, 1])
        c1.write(ticker)
        if c2.button("X", key=f"del_{ticker}"):
            watchlist.remove(ticker)
            doc_ref.set({"tickers": watchlist})
            st.rerun()

# Quản lý Thông số
param_ref = db.collection("system_config").document("wyckoff_params")
param_doc = param_ref.get()
sys_params = param_doc.to_dict() if param_doc.exists else {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}

with st.sidebar.expander("🎛️ Biến số Wyckoff", expanded=False):
    with st.form("param_form"):
        p_ma = st.number_input("Chu kỳ MA Volume", value=int(sys_params.get("vol_ma_period", 20)))
        p_sc = st.slider("Hệ số Vol Selling Climax", 1.5, 5.0, float(sys_params.get("sc_vol_multiplier", 2.5)))
        p_sp = st.slider("Ngưỡng Vol Spring (Cạn cung)", 0.1, 1.0, float(sys_params.get("spring_vol_ratio", 0.5)))
        p_tol = st.slider("Độ lệch đáy cho phép (%)", 1.0, 10.0, float((sys_params.get("spring_price_tolerance", 1.05)-1)*100))
        if st.form_submit_button("Lưu cấu hình"):
            sys_params = {"vol_ma_period": p_ma, "sc_vol_multiplier": p_sc, "spring_vol_ratio": p_sp, "spring_price_tolerance": 1 + (p_tol/100)}
            param_ref.set(sys_params)
            st.success("Đã lưu!")
            st.rerun()

# ==========================================
# 6. GIAO DIỆN CHÍNH
# ==========================================
st.title("Trạm Radar Wyckoff VSA Pro")

# Tải dữ liệu tín hiệu hiện tại
@st.cache_data(ttl=60)
def load_signals():
    docs = db.collection('wyckoff_signals').order_by('Date_Detected', direction=firestore.Query.DESCENDING).limit(50).stream()
    return pd.DataFrame([doc.to_dict() for doc in docs])

df_signals = load_signals()
col1, col2 = st.columns(2)
col1.metric("Tín hiệu đang theo dõi", len(df_signals) if not df_signals.empty else 0)
col2.metric("Lần cập nhật cuối", df_signals['Date_Detected'].iloc[0] if not df_signals.empty else "Chưa có")

# TAB SYSTEM
tab_scan, tab_chart, tab_alerts = st.tabs(["🚀 Quét Thị Trường", "📈 Biểu Đồ Phân Tích", "🔔 Cảnh Báo Telegram"])

# --- TAB 1: MÁY QUÉT TRỰC TIẾP ---
with tab_scan:
    st.markdown("### Kích hoạt Lõi AI Định lượng")
    if st.button("🚀 BẮT ĐẦU QUÉT TOÀN BỘ DANH MỤC", use_container_width=True):
        engine = WyckoffEngine(sys_params)
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        signals_found = 0
        total = len(watchlist)
        
        for i, ticker in enumerate(watchlist):
            status_text.text(f"Đang phân tích: {ticker}...")
            df = engine.get_data(ticker)
            if df is not None:
                result = engine.analyze(df)
                if result:
                    signal_data = {
                        "Date_Detected": datetime.now().strftime('%Y-%m-%d %H:%M'),
                        "Ticker": ticker,
                        "Price": result['Price'],
                        "Signal_Type": "Spring (Cạn cung)",
                        "TR_Top": result['TR_Top'],
                        "TR_Bottom": result['TR_Bottom'],
                        "Risk_Level": result['Risk_Level'],
                        "Timestamp": firestore.SERVER_TIMESTAMP
                    }
                    db.collection("wyckoff_signals").add(signal_data)
                    signals_found += 1
            
            progress_bar.progress((i + 1) / total)
            
        status_text.success(f"✅ Quét hoàn tất! Tìm thấy {signals_found} tín hiệu mới.")
        st.cache_data.clear() # Xóa cache để cập nhật bảng ngay
        
    st.markdown("---")
    st.markdown("### 📋 Bảng Khuyến Nghị Đầu Tư (Risk/Reward Matrix)")
    if not df_signals.empty:
        table_data = []
        for _, row in df_signals.iterrows():
            entry = row.get("Price", 0)
            tp = row.get("TR_Top", 0)
            sl = row.get("TR_Bottom", 0) * 0.98 # Cắt lỗ dưới đáy 2%
            
            rr = f"1 : {(tp-entry)/(entry-sl):.1f}" if (entry-sl) > 0 and tp > entry else "N/A"
            
            table_data.append({
                "Ngày": row.get("Date_Detected", "")[:10],
                "Mã": row.get("Ticker", ""),
                "Giá Hiện Tại": round(entry, 2),
                "Cắt Lỗ (SL)": round(sl, 2),
                "Chốt Lời (TP)": round(tp, 2),
                "Tỷ lệ R:R": rr,
                "Rủi Ro Khung D1": row.get("Risk_Level", "Chưa đánh giá")
            })
        st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)

# --- TAB 2: BIỂU ĐỒ TRỰC QUAN ---
with tab_chart:
    st.markdown("### Vẽ lại cấu trúc Cung/Cầu")
    if not df_signals.empty:
        tickers_with_signals = df_signals['Ticker'].unique().tolist()
        selected_ticker = st.selectbox("Chọn mã để xem biểu đồ:", tickers_with_signals)
        
        if selected_ticker:
            sig_info = df_signals[df_signals['Ticker'] == selected_ticker].iloc[0]
            engine = WyckoffEngine(sys_params)
            df_chart = engine.get_data(selected_ticker)
            
            if df_chart is not None:
                # Thêm MA200 và Vol MA để vẽ
                df_chart['MA200'] = df_chart['Close'].rolling(window=200).mean()
                df_chart['Vol_MA'] = df_chart['Volume'].rolling(window=int(sys_params.get("vol_ma_period", 20))).mean()
                
                fig = plot_wyckoff_chart(df_chart, selected_ticker, sig_info['TR_Top'], sig_info['TR_Bottom'])
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Chưa có tín hiệu để vẽ biểu đồ.")

# --- TAB 3: CẢNH BÁO TELEGRAM ---
with tab_alerts:
    st.markdown("### 🤖 Cài đặt Bot Telegram")
    st.write("Hệ thống sẽ tự động gửi tin nhắn đến điện thoại của bạn khi phát hiện siêu cổ phiếu.")
    
    tele_ref = db.collection("system_config").document("telegram")
    tele_doc = tele_ref.get()
    tele_data = tele_doc.to_dict() if tele_doc.exists else {"bot_token": "", "chat_id": ""}
    
    with st.form("tele_form"):
        bot_token = st.text_input("Bot Token (Lấy từ @BotFather):", value=tele_data.get("bot_token", ""))
        chat_id = st.text_input("Chat ID của bạn:", value=tele_data.get("chat_id", ""))
        
        if st.form_submit_button("Lưu cấu hình & Test"):
            tele_ref.set({"bot_token": bot_token, "chat_id": chat_id})
            # Test gửi tin nhắn
            if bot_token and chat_id:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {"chat_id": chat_id, "text": "✅ Hệ thống Wyckoff Radar đã kết nối thành công!"}
                try:
                    res = requests.post(url, json=payload)
                    if res.status_code == 200: st.success("Đã gửi tin nhắn test thành công tới Telegram!")
                    else: st.error(f"Lỗi Telegram: {res.text}")
                except Exception as e:
                    st.error(f"Lỗi mạng: {e}")
            st.rerun()
