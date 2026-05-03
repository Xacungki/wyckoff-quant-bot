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
    
    st.write("### Danh sách Báo cáo Chi tiết")
    # Hiển thị bảng dữ liệu toàn màn hình
    st.dataframe(df_signals, use_container_width=True, hide_index=True)
else:
    st.info("Hiện tại chưa có tín hiệu nào được ghi nhận trên cơ sở dữ liệu đám mây.")