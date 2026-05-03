import streamlit as st
import pandas as pd
import json
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# 1. CẤU HÌNH GIAO DIỆN LIGHT LUXURY
# ==========================================
st.set_page_config(page_title="Wyckoff Quant Radar", layout="wide")

st.markdown("""
    <style>
        /* Nền trắng off-white và chữ xám đậm chuyên nghiệp */
        .stApp {
            background-color: #FAF9F6;
            color: #333333;
        }
        /* Tùy chỉnh tiêu đề với viền vàng gold */
        h1 {
            color: #1A1A1A;
            border-bottom: 2px solid #D4AF37;
            padding-bottom: 10px;
            font-family: 'Helvetica Neue', sans-serif;
        }
        /* Tạo hiệu ứng đổ bóng mềm (soft shadow) cho bảng dữ liệu */
        .stDataFrame {
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
            border-radius: 8px;
            overflow: hidden;
        }
        /* Điểm nhấn màu vàng gold cho các chỉ số */
        div[data-testid="stMetricValue"] {
            color: #D4AF37;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. KẾT NỐI CƠ SỞ DỮ LIỆU ĐÁM MÂY
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        # Kiểm tra xem đang chạy trên Streamlit Cloud hay máy tính cá nhân
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
# MODULE: QUẢN LÝ DANH MỤC (WATCHLIST)
# ==========================================
st.sidebar.markdown("### ⚙️ Quản lý Danh mục Cổ phiếu")

# 1. Đọc danh sách hiện tại từ Database
doc_ref = db.collection("system_config").document("watchlist")
doc = doc_ref.get()

# Nếu chưa có kho lưu trữ, tự động tạo mới với vài mã mẫu
if not doc.exists:
    current_watchlist = ["FPT.VN", "VNM.VN", "AAPL", "NUS"]
    doc_ref.set({"tickers": current_watchlist})
else:
    current_watchlist = doc.to_dict().get("tickers", [])

# 2. Giao diện Thêm mã mới
new_ticker = st.sidebar.text_input("Thêm mã định lượng (VD: VIC.VN):")
if st.sidebar.button("➕ Thêm Mã"):
    if new_ticker and new_ticker.upper() not in current_watchlist:
        current_watchlist.append(new_ticker.upper())
        doc_ref.update({"tickers": current_watchlist})
        st.rerun() # Tải lại trang ngay lập tức

# 3. Giao diện Danh sách & Xóa mã
st.sidebar.markdown("---")
st.sidebar.markdown("**Danh sách đang rà soát:**")
for ticker in current_watchlist:
    col1, col2 = st.sidebar.columns([3, 1])
    col1.write(f"📈 {ticker}")
    if col2.button("❌", key=f"del_{ticker}"):
        current_watchlist.remove(ticker)
        doc_ref.update({"tickers": current_watchlist})
        st.rerun()

# ==========================================
# MODULE: BẢNG ĐIỀU KHIỂN BIẾN SỐ
# ==========================================
st.sidebar.markdown("---")
st.sidebar.markdown("### 🎛️ Bảng Điều Khiển Wyckoff")

# 1. Đọc thông số hiện tại từ Firestore
param_ref = db.collection("system_config").document("wyckoff_params")
param_doc = param_ref.get()

# Nếu chưa có, thiết lập bộ thông số mặc định chuẩn
if not param_doc.exists:
    default_params = {
        "vol_ma_period": 20,              # Chu kỳ MA Khối lượng
        "sc_vol_multiplier": 2.5,         # Hệ số đột biến Volume của Selling Climax
        "spring_vol_ratio": 0.5,          # Ngưỡng cạn kiệt Volume của Spring
        "spring_price_tolerance": 1.05    # Độ lệch giá cho phép tại đáy (%)
    }
    param_ref.set(default_params)
    current_params = default_params
else:
    current_params = param_doc.to_dict()

# 2. Xây dựng Form tương tác
with st.sidebar.form("param_form"):
    new_ma = st.number_input(
        "Chu kỳ MA Khối lượng", 
        min_value=10, max_value=50, 
        value=int(current_params.get("vol_ma_period", 20))
    )
    new_sc_mult = st.slider(
        "Hệ số Volume (Selling Climax)", 
        min_value=1.5, max_value=5.0, 
        value=float(current_params.get("sc_vol_multiplier", 2.5)), 
        step=0.1
    )
    new_spring_vol = st.slider(
        "Ngưỡng Volume cạn kiệt (Spring)", 
        min_value=0.1, max_value=1.0, 
        value=float(current_params.get("spring_vol_ratio", 0.5)), 
        step=0.1
    )
    new_tolerance = st.slider(
        "Độ lệch giá tại đáy cho phép (%)", 
        min_value=1.0, max_value=10.0, 
        value=float((current_params.get("spring_price_tolerance", 1.05) - 1) * 100), 
        step=0.5
    )

    submit_params = st.form_submit_button("Lưu Cấu Hình")
    
    # 3. Cập nhật thẳng vào Database khi nhấn Lưu
    if submit_params:
        updated_params = {
            "vol_ma_period": new_ma,
            "sc_vol_multiplier": new_sc_mult,
            "spring_vol_ratio": new_spring_vol,
            "spring_price_tolerance": 1 + (new_tolerance / 100)
        }
        param_ref.update(updated_params)
        st.success("✅ Đã cập nhật tham số định lượng!")
        st.rerun()

# ==========================================
# MODULE: GIAO DIỆN CHÍNH (MAIN DASHBOARD)
# ==========================================
st.title("Trạm Radar Tín Hiệu Định Lượng")
st.markdown("Hệ thống tự động theo dõi và bóc tách các điểm cạn kiệt nguồn cung (Spring) dựa trên mô hình Wyckoff VSA.")

# Hàm tải dữ liệu ĐỒNG NHẤT từ collection 'wyckoff_signals'
@st.cache_data(ttl=60)
def load_signals():
    docs = db.collection('wyckoff_signals').order_by('Date_Detected', direction=firestore.Query.DESCENDING).limit(50).stream()
    data = []
    for doc in docs:
        doc_data = doc.to_dict()
        data.append(doc_data)
    return pd.DataFrame(data)

df_signals = load_signals()

# Hiển thị tóm tắt chỉ số
col1, col2 = st.columns(2)
total_signals = len(df_signals) if not df_signals.empty else 0
latest_date = df_signals['Date_Detected'].iloc[0] if not df_signals.empty else "Chưa có dữ liệu"

col1.metric("Tổng Tín Hiệu Phát Hiện", total_signals)
col2.metric("Ngày Cập Nhật Gần Nhất", latest_date)

# KHỞI TẠO HỆ THỐNG TAB (Chia ngăn giao diện)
tab_radar, tab_knowledge = st.tabs(["📡 Radar Tín Hiệu", "🧠 Trạm Nạp Kiến Thức"])

with tab_radar:
    st.markdown("### Danh sách Báo cáo Chi tiết")
    
    if not df_signals.empty:
        data = []
        for index, sig in df_signals.iterrows():
            entry_price = sig.get("Price", 0)
            tr_top = sig.get("TR_Top", 0)
            tr_bottom = sig.get("TR_Bottom", 0)
            
            # Tính toán Risk/Reward (R:R)
            stop_loss = tr_bottom * 0.98 if tr_bottom else 0
            rr_ratio = "N/A"
            if entry_price > 0 and tr_top > entry_price and stop_loss > 0:
                risk = entry_price - stop_loss
                reward = tr_top - entry_price
                if risk > 0:
                    rr_ratio = f"1 : {reward/risk:.1f}"
            
            ticker = sig.get("Ticker", "")
            # Sửa lỗi hiển thị VN
            is_vn = ".VN" in ticker or ".HM" in ticker or ".HN" in ticker
            
            data.append({
                "Ngày": sig.get("Date_Detected", ""),
                "Mã CK": ticker,
                "Giá Vào": f"{entry_price:,.0f}" if is_vn else f"${entry_price:,.2f}",
                "Cắt Lỗ (SL)": f"{stop_loss:,.0f}" if is_vn and stop_loss else (f"${stop_loss:,.2f}" if stop_loss else "-"),
                "Chốt Lời (TP)": f"{tr_top:,.0f}" if is_vn and tr_top else (f"${tr_top:,.2f}" if tr_top else "-"),
                "Tỷ lệ R:R": rr_ratio,
                "Tín Hiệu": sig.get("Signal_Type", "")
            })

        st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
    else:
        st.info("Hiện chưa có tín hiệu mới nào đạt chuẩn.")

with tab_knowledge:
    st.subheader("🧠 Huấn luyện Tư duy cho AI")
    st.info("Nạp thêm link bài phân tích hoặc quy tắc mới để AI tự động nâng cấp bộ lọc thẩm định.")
    
    # Form nạp tri thức
    web_link = st.text_input("Dán link tài liệu (TradingView, Sách online, Bài báo...):")
    link_note = st.text_area("Ghi chú nhanh cho AI về link này (Không bắt buộc):")
    
    if st.button("Xác nhận Nạp Link"):
        if web_link:
            db.collection("knowledge_hub").add({
                "type": "link",
                "content": web_link,
                "note": link_note,
                "date_added": datetime.now().strftime("%Y-%m-%d %H:%M")
            })
            st.success("✅ Đã nạp thành công vào Bộ nhớ tri thức!")
            st.rerun()

    st.markdown("---")
    st.markdown("**Thư viện tri thức hiện có:**")
    # Hiển thị danh sách link đã nạp
    know_docs = db.collection("knowledge_hub").order_by("date_added", direction=firestore.Query.DESCENDING).stream()
    for k_doc in know_docs:
        item = k_doc.to_dict()
        st.write(f"🔗 {item.get('content')} - *{item.get('date_added')}*")
