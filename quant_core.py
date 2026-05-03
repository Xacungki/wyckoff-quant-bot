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
        self.yf_ticker = ticker.replace(".VN", ".HM")

    def fetch_daily_data(self, start_date, end_date):
        df = yf.download(self.yf_ticker, start=start_date, end=end_date, interval="1d", progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

# ==========================================
# KHỐI 2: LOGIC WYCKOFF VSA CHUYÊN SÂU (NHÓM 1)
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
        
        tr_bottom = df['Low'].iloc[sc_index:min(sc_index+4, len(df))].min()
        
        if sc_index + 15 < len(df):
            tr_top = df['High'].iloc[sc_index+1:sc_index+16].max()
        else:
            tr_top = df['High'].iloc[sc_index+1:].max()
            
        return float(tr_top), float(tr_bottom)

    # 1. TÍNH NĂNG MỚI: PHÂN TÍCH XU HƯỚNG TUẦN (WEEKLY TREND)
    def check_weekly_trend(self, df):
        if len(df) < 100: return "Không rõ"
        # Nén dữ liệu Ngày thành dữ liệu Tuần
        weekly_df = df.resample('W-FRI').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'})
        weekly_df = weekly_df.dropna()
        
        # Đường xu hướng MA30 Tuần (Tương đương đường trung bình 200 ngày)
        weekly_df['MA30'] = weekly_df['Close'].rolling(30).mean()
        
        if len(weekly_df) > 30 and weekly_df['Close'].iloc[-1] > weekly_df['MA30'].iloc[-1]:
            return "TĂNG (Uptrend)"
        return "GIẢM (Downtrend)"

    # 2. TÍNH NĂNG MỚI: NHẬN DIỆN MẪU HÌNH NẾN VSA ĐƠN LẺ
    def get_vsa_tags(self, df):
        if len(df) < 20: return ""
        tags = []
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        vol_ma = df['Volume'].rolling(20).mean().iloc[-1]
        
        spread = latest['High'] - latest['Low']
        prev_spread = prev['High'] - prev['Low']
        
        # No Supply Bar (Nến Cạn Cung): Giảm điểm, biên độ hẹp, Volume siêu thấp
        if latest['Close'] < prev['Close'] and spread < prev_spread and latest['Volume'] < prev['Volume'] and latest['Volume'] < (vol_ma * 0.7):
            tags.append("No Supply")
            
        # Stopping Volume (Hãm đà rơi): Gap down hoặc rơi mạnh nhưng rút chân, Volume khổng lồ
        if latest['Close'] < prev['Close'] and latest['Volume'] > (vol_ma * 2.0) and latest['Close'] > (latest['Low'] + spread * 0.5):
            tags.append("Stopping Vol")

        return ", ".join(tags) if tags else "Bình thường"

    # 3. TÍNH NĂNG MỚI: BỘ LỌC ĐA TÍN HIỆU (MUA / BÁN)
    def detect_advanced_signals(self, df, current_price, tr_top, tr_bottom):
        ma_period = self.params.get("vol_ma_period", 20)
        vol_ratio = self.params.get("spring_vol_ratio", 0.5)
        tolerance = self.params.get("spring_price_tolerance", 1.05)
        
        df['Vol_SMA'] = df['Volume'].rolling(window=ma_period).mean()
        latest_vol = float(df['Volume'].iloc[-1])
        latest_sma = float(df['Vol_SMA'].iloc[-1])
        latest_high = float(df['High'].iloc[-1])
        latest_low = float(df['Low'].iloc[-1])
        latest_close = float(df['Close'].iloc[-1])

        # TÍN HIỆU 1: SPRING (MUA) - Rơi về Hỗ trợ + Cạn cung
        if current_price <= (tr_bottom * tolerance) and latest_vol < (latest_sma * vol_ratio):
            return "Spring (Mua)"
            
        # TÍN HIỆU 2: BACK-UP / BU (MUA) - Vượt Kháng cự rồi test lại với Volume thấp
        if current_price >= (tr_top * 0.98) and current_price <= (tr_top * 1.05) and latest_vol < (latest_sma * vol_ratio):
            # Cần đảm bảo giá đã từng vượt qua Kháng cự trong 10 phiên trước
            recent_highs = df['High'].tail(10).max()
            if recent_highs > tr_top:
                return "Back-up (Mua)"

        # TÍN HIỆU 3: UPTHRUST (BÁN) - Đâm thủng Kháng cự nhưng đóng cửa thấp, Volume lớn (Phân phối đỉnh)
        if latest_high > tr_top and latest_close < (latest_high + latest_low) / 2 and latest_vol > (latest_sma * 1.5):
            return "Upthrust (Bán)"

        return None

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
    print("🚀 Bắt đầu chạy Lõi Quét tự động (Bản PRO)...")
    
    db_manager = FirestoreManager()
    db = db_manager.db
    if db is None: exit()
        
    try:
        doc_ref = db.collection("system_config").document("watchlist")
        doc = doc_ref.get()
        my_portfolio = doc.to_dict().get("tickers", []) if doc.exists else ["FPT.VN"]
    except:
        my_portfolio = ["FPT.VN"]
    
    try:
        param_ref = db.collection("system_config").document("wyckoff_params")
        param_doc = param_ref.get()
        sys_params = param_doc.to_dict() if param_doc.exists else {
            "vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05
        }
    except:
        sys_params = {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}
        
    print(f"📊 Đang tiến hành quét {len(my_portfolio)} mã...")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    vsa_engine = WyckoffVSASignal(sys_params)
    
    for ticker in my_portfolio:
        try:
            time.sleep(0.3)
            fetcher = QuantDataFetcher(ticker)
            df = fetcher.fetch_daily_data(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
            
            if df is not None and len(df) > 60:
                current_price = float(df['Close'].iloc[-1])
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
                
                if tr_top is None or tr_bottom is None: continue 
                
                # Quét 3 Loại Tín hiệu
                signal_type = vsa_engine.detect_advanced_signals(df, current_price, tr_top, tr_bottom)
                
                if signal_type:
                    rs_score = round(((current_price - float(df['Close'].iloc[-60])) / float(df['Close'].iloc[-60])) * 100, 2)
                    weekly_trend = vsa_engine.check_weekly_trend(df)
                    vsa_tags = vsa_engine.get_vsa_tags(df)
                        
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
                    print(f"🔥 {ticker}: {signal_type} | Tuần: {weekly_trend} | VSA: {vsa_tags}")
                    db_manager.push_signal(signal_data)
                    
        except Exception as e:
            print(f"Lỗi: {e}")
            
    print(f"✅ Hoàn tất quét ngày {end_date.strftime('%Y-%m-%d')}.")
