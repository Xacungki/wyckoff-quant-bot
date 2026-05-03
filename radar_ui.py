import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

# 1. CẤU HÌNH GIAO DIỆN LIGHT LUXURY
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

import json # Thêm thư viện này ở đầu file

# 2. KẾT NỐI CƠ SỞ DỮ LIỆU ĐÁM MÂY
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        # Kiểm tra xem đang chạy trên Streamlit Cloud hay máy tính cá nhân
        if "FIREBASE_JSON" in st.secrets:
            # Nếu trên Cloud, lấy chìa khóa từ két sắt của Streamlit
            key_dict = json.loads(st.secrets["FIREBASE_JSON"])
            cred = credentials.Certificate(key_dict)
        else:
            # Nếu chạy trên máy Mac, vẫn dùng file local
            cred = credentials.Certificate("firebase_key.json")
            
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = init_firebase()
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

# 3. HÀM TẢI DỮ LIỆU TỪ FIRESTORE
def load_signals():
    # Kéo dữ liệu từ collection 'vsa_signals', sắp xếp mới nhất lên đầu
    docs = db.collection('vsa_signals').order_by('Timestamp', direction=firestore.Query.DESCENDING).limit(50).stream()
    data = []
    for doc in docs:
        doc_data = doc.to_dict()
        # Loại bỏ trường Timestamp thô để hiển thị đẹp hơn
        if 'Timestamp' in doc_data:
            del doc_data['Timestamp']
        data.append(doc_data)
    
    if data:
        # Sắp xếp lại thứ tự cột cho lô-gic
        df = pd.DataFrame(data)
        return df[['Date_Detected', 'Ticker', 'Price', 'Signal_Type', 'Status']]
    return pd.DataFrame()

# 4. THIẾT KẾ BỐ CỤC HIỂN THỊ
st.title("Trạm Radar Tín Hiệu Định Lượng")
st.markdown("Hệ thống tự động theo dõi và bóc tách các điểm cạn kiệt nguồn cung (Spring) dựa trên mô hình Wyckoff VSA.")

# Tải dữ liệu
df_signals = load_signals()

if not df_signals.empty:
    # Hiển thị tóm tắt chỉ số
    col1, col2 = st.columns(2)
    col1.metric("Tổng Tín Hiệu Phát Hiện", len(df_signals))
    col2.metric("Ngày Cập Nhật Gần Nhất", df_signals['Date_Detected'].iloc[0])
    
    import pandas as pd # Đảm bảo dòng này có ở đầu file

# ... (các phần kết nối db ở trên giữ nguyên) ...

st.markdown("### Danh sách Báo cáo Chi tiết")

# 1. Lấy dữ liệu từ Firestore (Sắp xếp mới nhất lên đầu)
signals_ref = db.collection("wyckoff_signals").order_by("Date_Detected", direction=firestore.Query.DESCENDING).limit(30)
docs = signals_ref.stream()

data = []
for doc in docs:
    sig = doc.to_dict()
    
    # 2. Rút trích Dữ liệu Bối cảnh
    entry_price = sig.get("Price", 0)
    tr_top = sig.get("TR_Top", 0)
    tr_bottom = sig.get("TR_Bottom", 0)
    
    # 3. TÍNH TOÁN QUẢN TRỊ VỐN (RISK:REWARD)
    # Stoploss: Đặt dưới đáy TR 2% để tránh quét râu nến (Shakeout)
    stop_loss = tr_bottom * 0.98 if tr_bottom else 0
    
    rr_ratio = "N/A"
    if entry_price > 0 and tr_top > entry_price and stop_loss > 0:
        risk = entry_price - stop_loss
        reward = tr_top - entry_price
        if risk > 0:
            rr = reward / risk
            rr_ratio = f"1 : {rr:.1f}" # Ví dụ: 1 : 3.5
            
    # Định dạng tiền tệ: VNĐ thì không số thập phân, USD thì 2 số
    ticker = sig.get("Ticker", "")
    is_vn = ".VN" in ticker
    
    data.append({
        "Ngày": sig.get("Date_Detected", ""),
        "Mã CK": ticker,
        "Giá Vào (Entry)": f"{entry_price:,.0f}" if is_vn else f"${entry_price:,.2f}",
        "Cắt Lỗ (SL)": f"{stop_loss:,.0f}" if is_vn and stop_loss else (f"${stop_loss:,.2f}" if stop_loss else "-"),
        "Chốt Lời (TP)": f"{tr_top:,.0f}" if is_vn and tr_top else (f"${tr_top:,.2f}" if tr_top else "-"),
        "Tỷ lệ R:R": rr_ratio,
        "Tín Hiệu": sig.get("Signal_Type", "")
    })

# 4. Render Bảng dữ liệu lên Web
if data:
    df = pd.DataFrame(data)
    
    # CSS Highlight cho cột R:R bằng công cụ của Streamlit
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("Chưa có tín hiệu cạn cung nào được ghi nhận.")
