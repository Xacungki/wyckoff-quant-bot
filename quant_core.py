import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# KHỐI 1: KẾT NỐI DATABASE & CẤU HÌNH
# ==========================================
def init_firebase():
    if not firebase_admin._apps:
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

# ==========================================
# KHỐI 2: XỬ LÝ DỮ LIỆU
# ==========================================
class QuantDataFetcher:
    def __init__(self, ticker):
        self.original_ticker = ticker
        # Yahoo Finance dùng .HM cho sàn HOSE, .HN cho HNX. Map tạm thời nếu người dùng nhập .VN
        self.yf_ticker = ticker.replace(".VN", ".HM") 

    def fetch_daily_data(self, start_date, end_date):
        df = yf.download(self.yf_ticker, start=start_date, end=end_date, interval="1d", progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

# ==========================================
# KHỐI 3: LOGIC WYCKOFF VSA NÂNG CAO
# ==========================================
class WyckoffVSASignal:
    def __init__(self, sys_params):
        self.params = sys_params

    def identify_trading_range(self, df):
        if len(df) < 60:
            return None, None
            
        ma_period = self.params.get("vol_ma_period", 20)
        sc_mult = self.params.get("sc_vol_multiplier", 2.5)
        
        # Tìm Selling Climax (Nến giảm mạnh + Volume đột biến)
        df['Vol_MA'] = df['Volume'].rolling(window=ma_period).mean()
        df['Is_SC'] = (df['Volume'] > df['Vol_MA'] * sc_mult) & (df['Close'] < df['Open'])
        
        recent_data = df.tail(60) # Xét trong 3 tháng gần nhất
        sc_candles = recent_data[recent_data['Is_SC'] == True]
        
        if sc_candles.empty:
            return None, None
            
        # Lấy ngày xảy ra SC có Volume lớn nhất
        sc_date = sc_candles['Volume'].idxmax()
        sc_index = df.index.get_loc(sc_date)
        
        # Xác định TR_Bottom (Đáy) từ SC
        # Đáy có thể là giá thấp nhất trong 3 phiên quanh SC
        tr_bottom = df['Low'].iloc[sc_index:min(sc_index+4, len(df))].min()
        
        # Xác định TR_Top (Đỉnh Automatic Rally)
        # Đỉnh là điểm phục hồi cao nhất trong khoảng 15-20 phiên sau SC
        if sc_index + 20 < len(df):
            tr_top = df['High'].iloc[sc_index+1:sc_index+21].max()
        else:
            tr_top = df['High'].iloc[sc_index+1:].max()
            
        return float(tr_top), float(tr_bottom)

    def detect_supply_exhaustion(self, df, current_price, tr_bottom):
        ma_period = self.params.get("vol_ma_period", 20)
        vol_ratio = self.params.get("spring_vol_ratio", 0.5)
        tolerance = self.params.get("spring_price_tolerance", 1.05)

        # 1. Điều kiện giá: Nằm sát vùng đáy (TR_Bottom)
        is_near_support = current_price <= (tr_bottom * tolerance)
        
        # 2. Điều kiện Volume: Volume hiện tại cạn kiệt so với trung bình
        df['Vol_SMA'] = df['Volume'].rolling(window=ma_period).mean()
        latest_vol = df['Volume'].iloc[-1]
        latest_sma = df['Vol_SMA'].iloc[-1]
        
        is_low_volume = latest_vol < (latest_sma * vol_ratio)
        
        # Trả về boolean thay vì DataFrame để tránh lỗi Logic
        return is_near_support and is_low_volume

# ==========================================
# KHỐI 4: VẬN HÀNH CHÍNH (CRON-JOB)
# ==========================================
def main():
    print("🚀 Bắt đầu chạy Lõi Quét Wyckoff tự động...")
    db = init_firebase()
    
    # 1. LẤY DANH MỤC VÀ THÔNG SỐ TỪ FIREBASE
    watchlist_doc = db.collection("system_config").document("watchlist").get()
    my_portfolio = watchlist_doc.to_dict().get("tickers", []) if watchlist_doc.exists else ["FPT.VN", "VNM.VN", "AAPL"]
    
    param_doc = db.collection("system_config").document("wyckoff_params").get()
    if param_doc.exists:
        sys_params = param_doc.to_dict()
    else:
        sys_params = {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}
    
    print(f"⚙️ Cấu hình: {sys_params}")
    print(f"📊 Đang quét {len(my_portfolio)} mã: {my_portfolio}")
    
    # 2. THIẾT LẬP THỜI GIAN QUÉT (Tự động 1 năm gần nhất)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    vsa_engine = WyckoffVSASignal(sys_params)
    signals_found = 0
    
    for ticker in my_portfolio:
        try:
            print(f"  -> Phân tích: {ticker}")
            fetcher = QuantDataFetcher(ticker)
            df = fetcher.fetch_daily_data(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            
            if df is None or df.empty:
                print(f"     [!] Không lấy được dữ liệu cho {ticker}")
                continue
                
            current_price = float(df['Close'].iloc[-1])
            
            # BƯỚC 1: Tìm Khung giá (Trading Range)
            tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
            
            if tr_top is None or tr_bottom is None:
                continue # Chưa có nhịp SC để tạo khung giá
            
            # BƯỚC 2: Kiểm tra cạn cung (Spring) tại đáy
            is_spring = vsa_engine.detect_supply_exhaustion(df, current_price, tr_bottom)
            
            if is_spring:
                signal_data = {
                    "Date_Detected": df.index[-1].strftime('%Y-%m-%d'),
                    "Ticker": ticker,
                    "Price": round(current_price, 2),
                    "Signal_Type": "Cạn cung (Spring) tại Hỗ trợ",
                    "TR_Top": round(tr_top, 2),
                    "TR_Bottom": round(tr_bottom, 2),
                    "Status": "Mới phát hiện",
                    "Timestamp": firestore.SERVER_TIMESTAMP
                }
                print(f"     🔥 CẢNH BÁO: {ticker} đang có tín hiệu Cạn Cung (Spring)!")
                
                # Đẩy lên Firebase đồng nhất 1 Collection
                db.collection("wyckoff_signals").add(signal_data)
                signals_found += 1
                
        except Exception as e:
            print(f"     [!] Lỗi khi quét {ticker}: {e}")

    print("="*50)
    print(f"✅ Hoàn tất! Phát hiện {signals_found} tín hiệu đạt chuẩn hôm nay.")
    print("="*50)

if __name__ == "__main__":
    main()
