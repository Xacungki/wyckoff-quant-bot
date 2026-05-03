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
        .stApp { background-color: #FAF9F6; color: #333333; }
        h1 { color: #1A1A1A; border-bottom: 2px solid #D4AF37; padding-bottom: 10px; font-family: 'Helvetica Neue', sans-serif; }
        .stDataFrame { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); border-radius: 8px; overflow: hidden; }
        div[data-testid="stMetricValue"] { color: #D4AF37; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# BỘ TỪ ĐIỂN CỔ PHIẾU THEO NGÀNH (MỚI)
# ==========================================
SECTORS = {
    "🏦 Ngân hàng": ["VCB.VN", "BID.VN", "CTG.VN", "TCB.VN", "VPB.VN", "MBB.VN", "ACB.VN", "STB.VN", "HDB.VN", "VIB.VN", "TPB.VN", "SHB.VN", "EIB.VN", "MSB.VN", "OCB.VN", "LPB.VN"],
    "🏢 Bất động sản": ["VHM.VN", "VIC.VN", "VRE.VN", "NVL.VN", "DIG.VN", "DXG.VN", "KDH.VN", "NLG.VN", "PDR.VN", "CEO.VN", "HDG.VN", "DXS.VN", "CRE.VN"],
    "📈 Chứng khoán": ["SSI.VN", "VND.VN", "VCI.VN", "HCM.VN", "SHS.VN", "MBS.VN", "VIX.VN", "FTS.VN", "BSI.VN", "CTS.VN", "AGR.VN"],
    "🏭 Thép & Vật liệu": ["HPG.VN", "HSG.VN", "NKG.VN", "HT1.VN", "BCC.VN", "POM.VN", "SMC.VN"],
    "🛒 Bán lẻ": ["MWG.VN", "PNJ.VN", "FRT.VN", "DGW.VN", "PET.VN", "HAX.VN"],
    "💻 Công nghệ": ["FPT.VN", "CMG.VN", "ELC.VN", "ITD.VN", "VGI.VN", "CTR.VN"],
    "⚡ Năng lượng": ["GAS.VN", "POW.VN", "PLX.VN", "PVD.VN", "PVS.VN", "NT2.VN", "GEG.VN", "PC1.VN", "REE.VN"],
    "📦 Cảng & Logistics": ["GMD.VN", "HAH.VN", "VSC.VN", "SGP.VN", "MVN.VN", "VOS.VN"],
    "🐟 Nông Lâm Thủy Sản": ["VHC.VN", "ANV.VN", "FMC.VN", "DBC.VN", "HAG.VN", "BAF.VN", "LTG.VN", "TAR.VN", "PAN.VN", "IDI.VN"],
    "🏗️ Xây dựng & Đầu tư công": ["VCG.VN", "HHV.VN", "C4G.VN", "LCG.VN", "FCN.VN", "HBC.VN", "CTD.VN", "HUT.VN"],
    "🏭 Khu Công Nghiệp": ["BCM.VN", "IDC.VN", "KBC.VN", "SZC.VN", "PHR.VN", "NTC.VN", "VGC.VN", "SIP.VN"],
    "💊 Y tế & Hóa chất": ["DGC.VN", "DPM.VN", "DCM.VN", "CSV.VN", "DHG.VN", "IMP.VN", "DBD.VN"]
}

# Tạo bộ tra cứu ngược (Mã -> Tên Ngành) để hiển thị trong Bảng
TICKER_TO_SECTOR = {}
for sector_name, tickers in SECTORS.items():
    clean_name = sector_name.split(" ", 1)[1] # Cắt bỏ icon emoji
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
# MODULE: QUẢN LÝ DANH MỤC (WATCHLIST)
# ==========================================
st.sidebar.markdown("### ⚙️ Quản lý Danh mục")

doc_ref = db.collection("system_config").document("watchlist")
doc = doc_ref.get()

if not doc.exists:
    current_watchlist = ["FPT.VN", "VNM.VN", "AAPL", "NUS"]
    doc_ref.set({"tickers": current_watchlist})
else:
    current_watchlist = doc.to_dict().get("tickers", [])

# TÍNH NĂNG 1: Thêm 1 mã lẻ
new_ticker = st.sidebar.text_input("Thêm mã đơn lẻ (VD: VIC.VN):")
if st.sidebar.button("➕ Thêm Mã"):
    if new_ticker and new_ticker.upper() not in current_watchlist:
        current_watchlist.append(new_ticker.upper())
        doc_ref.update({"tickers": current_watchlist})
        st.rerun()

# TÍNH NĂNG 2: Thêm tự động cả Ngành
st.sidebar.markdown("---")
st.sidebar.markdown("**Lọc tự động theo Ngành:**")
selected_sector = st.sidebar.selectbox("Chọn nhóm ngành:", list(SECTORS.keys()))
if st.sidebar.button("📥 Thêm toàn bộ Ngành này"):
    added_count = 0
    for t in SECTORS[selected_sector]:
        if t not in current_watchlist:
            current_watchlist.append(t)
            added_count += 1
    
    if added_count > 0:
        doc_ref.update({"tickers": current_watchlist})
        st.sidebar.success(f"Đã thêm {added_count} mã ngành {selected_sector.split(' ', 1)[1]}!")
        st.rerun()
    else:
        st.sidebar.info("Toàn bộ mã ngành này đã có sẵn.")

# TÍNH NĂNG 3: Xóa & Xem Danh sách
st.sidebar.markdown("---")
st.sidebar.markdown(f"**Đang rà soát: {len(current_watchlist)} mã**")

if st.sidebar.button("🗑️ XÓA TOÀN BỘ DANH SÁCH"):
    doc_ref.update({"tickers": []})
    st.rerun()

with st.sidebar.expander("Xem chi tiết các mã đang quét", expanded=False):
    for ticker in current_watchlist:
        col1, col2 = st.columns([3, 1])
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

param_ref = db.collection("system_config").document("wyckoff_params")
param_doc = param_ref.get()

if not param_doc.exists:
    current_params = {
        "vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05
    }
    param_ref.set(current_params)
else:
    current_params = param_doc.to_dict()

with st.sidebar.form("param_form"):
    new_ma = st.number_input("Chu kỳ MA Khối lượng", min_value=10, max_value=50, value=int(current_params.get("vol_ma_period", 20)))
    new_sc_mult = st.slider("Hệ số Volume (Selling Climax)", 1.5, 5.0, float(current_params.get("sc_vol_multiplier", 2.5)), 0.1)
    new_spring_vol = st.slider("Ngưỡng Volume cạn kiệt (Spring)", 0.1, 1.0, float(current_params.get("spring_vol_ratio", 0.5)), 0.1)
    new_tolerance = st.slider("Độ lệch giá tại đáy cho phép (%)", 1.0, 10.0, float((current_params.get("spring_price_tolerance", 1.05) - 1) * 100), 0.5)

    if st.form_submit_button("Lưu Cấu Hình"):
        param_ref.update({
            "vol_ma_period": new_ma, "sc_vol_multiplier": new_sc_mult,
            "spring_vol_ratio": new_spring_vol, "spring_price_tolerance": 1 + (new_tolerance / 100)
        })
        st.success("✅ Đã cập nhật tham số!")
        st.rerun()

# ==========================================
# MODULE: GIAO DIỆN CHÍNH (MAIN DASHBOARD)
# ==========================================
st.title("Trạm Radar Tín Hiệu Định Lượng")
st.markdown("Hệ thống tự động theo dõi và bóc tách các điểm cạn kiệt nguồn cung (Spring) dựa trên mô hình Wyckoff VSA.")

@st.cache_data(ttl=60)
def load_signals():
    docs = db.collection('wyckoff_signals').order_by('Date_Detected', direction=firestore.Query.DESCENDING).limit(50).stream()
    return pd.DataFrame([doc.to_dict() for doc in docs])

df_signals = load_signals()

col1, col2 = st.columns(2)
total_signals = len(df_signals) if not df_signals.empty else 0
latest_date = df_signals['Date_Detected'].iloc[0] if not df_signals.empty else "Chưa có dữ liệu"

col1.metric("Tổng Tín Hiệu Phát Hiện", total_signals)
col2.metric("Ngày Cập Nhật Gần Nhất", latest_date)

tab_radar, tab_knowledge = st.tabs(["📡 Radar Tín Hiệu", "🧠 Trạm Nạp Kiến Thức"])

with tab_radar:
    st.markdown("### Danh sách Báo cáo Chi tiết")
    
    if not df_signals.empty:
        data = []
        for index, sig in df_signals.iterrows():
            entry_price = sig.get("Price", 0)
            tr_top = sig.get("TR_Top", 0)
            tr_bottom = sig.get("TR_Bottom", 0)
            
            stop_loss = tr_bottom * 0.98 if tr_bottom else 0
            rr_ratio = "N/A"
            if entry_price > 0 and tr_top > entry_price and stop_loss > 0:
                risk = entry_price - stop_loss
                reward = tr_top - entry_price
                if risk > 0: rr_ratio = f"1 : {reward/risk:.1f}"
            
            ticker = sig.get("Ticker", "")
            is_vn = ".VN" in ticker or ".HM" in ticker or ".HN" in ticker
            sector = TICKER_TO_SECTOR.get(ticker, "Khác") # Dò Tên Ngành
            
            data.append({
                "Ngày": sig.get("Date_Detected", ""),
                "Mã CK": ticker,
                "Nhóm Ngành": sector,
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
    
    web_link = st.text_input("Dán link tài liệu (TradingView, Sách online, Bài báo...):")
    link_note = st.text_area("Ghi chú nhanh cho AI về link này (Không bắt buộc):")
    
    if st.button("Xác nhận Nạp Link"):
        if web_link:
            db.collection("knowledge_hub").add({
                "type": "link", "content": web_link, "note": link_note,
                "date_added": datetime.now().strftime("%Y-%m-%d %H:%M")
            })
            st.success("✅ Đã nạp thành công vào Bộ nhớ tri thức!")
            st.rerun()

    st.markdown("---")
    st.markdown("**Thư viện tri thức hiện có:**")
    know_docs = db.collection("knowledge_hub").order_by("date_added", direction=firestore.Query.DESCENDING).stream()
    for k_doc in know_docs:
        item = k_doc.to_dict()
        st.write(f"🔗 {item.get('content')} - *{item.get('date_added')}*")
