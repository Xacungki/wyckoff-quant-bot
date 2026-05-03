import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
import time

# ==========================================
# KHỐI 1: LẤY DỮ LIỆU
# ==========================================
class QuantDataFetcher:
    def __init__(self, ticker):
        self.ticker = ticker
        # Xử lý tự động map mã Việt Nam (.VN sang .HM cho HOSE để YFinance hiểu được)
        self.yf_ticker = ticker.replace(".VN", ".HM")

    def fetch_daily_data(self, start_date, end_date):
        df = yf.download(self.yf_ticker, start=start_date, end=end_date, interval="1d", progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

# ==========================================
# KHỐI 2: LOGIC WYCKOFF VSA
# ==========================================
class WyckoffVSASignal:
    def __init__(self, sys_params):
        self.params = sys_params

    def identify_trading_range(self, df):
        if len(df) < 50:
            return None, None
            
        ma_period = self.params.get("vol_ma_period", 20)
        sc_mult = self.params.get("sc_vol_multiplier", 2.5)
        
        df['Vol_MA'] = df['Volume'].rolling(window=ma_period).mean()
        df['Is_SC'] = (df['Volume'] > df['Vol_MA'] * sc_mult) & (df['Close'] < df['Open'])
        
        recent_data = df.tail(60)
        sc_candles = recent_data[recent_data['Is_SC'] == True]
        
        if sc_candles.empty:
            return None, None
            
        sc_date = sc_candles['Volume'].idxmax()
        sc_index = df.index.get_loc(sc_date)
        
        # Tìm Đáy và Đỉnh của Khung Giá (Trading Range)
        tr_bottom = df['Low'].iloc[sc_index:min(sc_index+4, len(df))].min()
        
        if sc_index + 15 < len(df):
            tr_top = df['High'].iloc[sc_index+1:sc_index+16].max()
        else:
            tr_top = df['High'].iloc[sc_index+1:].max()
            
        return float(tr_top), float(tr_bottom)

    def detect_supply_exhaustion(self, df, current_price, tr_bottom):
        ma_period = self.params.get("vol_ma_period", 20)
        vol_ratio = self.params.get("spring_vol_ratio", 0.5)
        tolerance = self.params.get("spring_price_tolerance", 1.05)

        # 1. Điều kiện giá: Nằm sát vùng đáy (TR_Bottom) theo tolerance
        is_near_support = current_price <= (tr_bottom * tolerance)
        
        # 2. Điều kiện Volume: Volume hiện tại cạn kiệt so với trung bình
        df['Vol_SMA'] = df['Volume'].rolling(window=ma_period).mean()
        latest_vol = float(df['Volume'].iloc[-1])
        latest_sma = float(df['Vol_SMA'].iloc[-1])
        
        is_low_volume = latest_vol < (latest_sma * vol_ratio)
        
        return is_near_support and is_low_volume

# ==========================================
# KHỐI 3: QUẢN TRỊ CƠ SỞ DỮ LIỆU ĐÁM MÂY
# ==========================================
class FirestoreManager:
    def __init__(self, key_path="firebase_key.json"):
        try:
            cred = credentials.Certificate(key_path)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("[+] Đã kết nối thành công tới Database Đám mây!")
        except Exception as e:
            print(f"[!] Lỗi kết nối Database: {e}")
            self.db = None

    def push_signal(self, signal_data):
        if not self.db: return
        try:
            doc_ref = self.db.collection('wyckoff_signals').document()
            doc_ref.set(signal_data)
        except Exception as e:
            print(f"Lỗi khi đẩy dữ liệu mã {signal_data.get('Ticker')}: {e}")

# ==========================================
# KHỐI 4: KÍCH HOẠT HỆ THỐNG QUÉT TỰ ĐỘNG
# ==========================================
if __name__ == "__main__":
    print("🚀 Bắt đầu chạy Lõi Quét tự động...")
    
    db_manager = FirestoreManager()
    db = db_manager.db
    
    if db is None:
        print("[!] Không thể kết nối Database. Dừng chương trình.")
        exit()
        
    try:
        doc_ref = db.collection("system_config").document("watchlist")
        doc = doc_ref.get()
        my_portfolio = doc.to_dict().get("tickers", []) if doc.exists else ["FPT.VN", "VNM.VN", "AAPL"]
    except:
        my_portfolio = ["FPT.VN", "VNM.VN", "AAPL", "NUS"]
    
    try:
        param_ref = db.collection("system_config").document("wyckoff_params")
        param_doc = param_ref.get()
        sys_params = param_doc.to_dict() if param_doc.exists else {
            "vol_ma_period": 20, 
            "sc_vol_multiplier": 2.5, 
            "spring_vol_ratio": 0.5, 
            "spring_price_tolerance": 1.05
        }
    except:
        sys_params = {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}
        
    print(f"⚙️ Áp dụng thông số cấu hình: {sys_params}")
    print(f"📊 Đang tiến hành quét {len(my_portfolio)} mã: {my_portfolio}")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    vsa_engine = WyckoffVSASignal(sys_params)
    
    for ticker in my_portfolio:
        try:
            time.sleep(0.3) # Tạm nghỉ để tránh bị chặn IP
            fetcher = QuantDataFetcher(ticker)
            df = fetcher.fetch_daily_data(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
            
            if df is not None and not df.empty:
                current_price = float(df['Close'].iloc[-1])
                
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
                
                if tr_top is None or tr_bottom is None:
                    continue 
                
                is_spring = vsa_engine.detect_supply_exhaustion(df, current_price, tr_bottom) 
                
                if is_spring:
                    # BỔ SUNG: Tính điểm RS Sức Mạnh Dòng Tiền (Dựa trên hiệu suất 60 ngày)
                    rs_score = 0
                    if len(df) >= 60:
                        price_60d = float(df['Close'].iloc[-60])
                        rs_score = round(((current_price - price_60d) / price_60d) * 100, 2)
                        
                    signal_data = {
                        "Date_Detected": df.index[-1].strftime('%Y-%m-%d'),
                        "Ticker": ticker,
                        "Price": float(current_price),
                        "Signal_Type": "Cạn cung (Spring) trong Trading Range",
                        "TR_Top": float(tr_top),
                        "TR_Bottom": float(tr_bottom),
                        "RS_Score": rs_score, # Bắn chỉ số RS lên Cloud
                        "Status": "Mới phát hiện",
                        "Timestamp": firestore.SERVER_TIMESTAMP
                    }
                    print(f"🔥 Phát hiện {ticker} cạn cung | RS Sức mạnh: {rs_score}%")
                    db_manager.push_signal(signal_data)
                    
        except Exception as e:
            print(f"Lỗi khi quét {ticker}: {e}")
            
    print("="*50)
    print(f"✅ Hoàn tất quá trình quét ngày {end_date.strftime('%Y-%m-%d')}.")
