import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
import time
import warnings
import requests

# Tắt cảnh báo rác
warnings.filterwarnings('ignore')

# ---------------------------------------------------------
# LỚP GIÁP BẢO VỆ: KIỂM TRA THƯ VIỆN DỮ LIỆU
# ---------------------------------------------------------
try:
    from vnstock import stock_historical_data
    VNSTOCK_AVAILABLE = True
except ImportError:
    VNSTOCK_AVAILABLE = False

import yfinance as yf

# ==========================================
# KHỐI 1: LẤY DỮ LIỆU (TỰ ĐỘNG CHUYỂN ĐỔI VNSTOCK <-> YFINANCE)
# ==========================================
class QuantDataFetcher:
    def __init__(self, ticker):
        self.original_ticker = str(ticker).upper().strip()
        self.base_ticker = self.original_ticker.replace(".VN", "").replace(".HM", "").replace(".HN", "").replace(".UP", "")

    def fetch_daily_data(self, start_date, end_date):
        if VNSTOCK_AVAILABLE:
            try:
                df = stock_historical_data(symbol=self.base_ticker, start_date=start_date, end_date=end_date, resolution='1D', type='stock')
                if df is not None and not df.empty and len(df) > 10:
                    df = df.rename(columns={'time': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
                    df['Date'] = pd.to_datetime(df['Date'])
                    df.set_index('Date', inplace=True)
                    clean_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    clean_df = clean_df[clean_df['Volume'] > 0]
                    if len(clean_df) > 50:
                        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                            clean_df[col] = pd.to_numeric(clean_df[col], errors='coerce')
                        return clean_df
            except Exception:
                pass 

        suffixes = [".HM", ".HN", ".UP", ""]
        for suffix in suffixes:
            yf_ticker = f"{self.base_ticker}{suffix}"
            try:
                df = yf.download(yf_ticker, start=start_date, end=end_date, interval="1d", auto_adjust=False, progress=False)
                if df is not None and not df.empty and len(df) > 10:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    if 'Close' in df.columns:
                        clean_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                        clean_df = clean_df[clean_df['Volume'] > 0]
                        if len(clean_df) > 50:
                            return clean_df
            except Exception:
                continue
        return None

# ==========================================
# KHỐI 2: LOGIC WYCKOFF VSA & TÍNH NĂNG PRO MỚI
# ==========================================
class WyckoffVSASignal:
    def __init__(self, sys_params):
        self.params = sys_params

    def identify_trading_range(self, df):
        if len(df) < 50: return None, None
            
        ma_period = self.params.get("vol_ma_period", 20)
        sc_mult = self.params.get("sc_vol_multiplier", 2.5)
        
        df['Vol_MA'] = df['Volume'].rolling(window=ma_period).mean()
        df['Is_SC'] = (df['Volume'] > df['Vol_MA'] * sc_mult) & (df['Close'] < df['Open'])
        
        recent_data = df.tail(150)
        sc_candles = recent_data[recent_data['Is_SC'] == True]
        
        if sc_candles.empty: return None, None
            
        sc_date = sc_candles['Volume'].idxmax()
        sc_index = df.index.get_loc(sc_date)
        
        tr_bottom = df['Low'].iloc[sc_index:min(sc_index+4, len(df))].min()
        if sc_index + 15 < len(df):
            tr_top = df['High'].iloc[sc_index+1:sc_index+16].max()
        else:
            tr_top = df['High'].iloc[sc_index+1:].max()
            
        return float(tr_top), float(tr_bottom)

    # TÍNH NĂNG MỚI 1: TÌM ĐIỂM POC (Point of Control) THEO VOLUME PROFILE
    def calculate_poc(self, df, tr_bottom, tr_top):
        try:
            # Chỉ lấy dữ liệu trong Khung Giá (Trong biên độ)
            tr_data = df[(df['Close'] >= tr_bottom * 0.95) & (df['Close'] <= tr_top * 1.05)].tail(100)
            if tr_data.empty: return None
            # Phân bổ khối lượng theo mức giá (20 bins)
            bins = np.linspace(tr_bottom * 0.95, tr_top * 1.05, 20)
            tr_data['Price_Bin'] = pd.cut(tr_data['Close'], bins=bins)
            poc_bin = tr_data.groupby('Price_Bin')['Volume'].sum().idxmax()
            poc_price = poc_bin.mid
            return round(float(poc_price), 2)
        except Exception:
            return None

    # TÍNH NĂNG MỚI 2: TÍNH ATR (Average True Range) CHO TRAILING STOP
    def calculate_atr(self, df, period=14):
        try:
            high_low = df['High'] - df['Low']
            high_close = np.abs(df['High'] - df['Close'].shift())
            low_close = np.abs(df['Low'] - df['Close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            atr = true_range.rolling(period).mean()
            return round(float(atr.iloc[-1]), 2)
        except Exception:
            return 0

    def check_weekly_trend(self, df):
        if len(df) < 100: return "Không rõ"
        try:
            weekly_df = df.resample('W-FRI').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'})
            weekly_df = weekly_df.dropna()
            weekly_df['MA30'] = weekly_df['Close'].rolling(30).mean()
            if len(weekly_df) > 30 and weekly_df['Close'].iloc[-1] > weekly_df['MA30'].iloc[-1]:
                return "TĂNG (Uptrend)"
            return "GIẢM (Downtrend)"
        except: return "Không rõ"

    def get_vsa_tags(self, df):
        if len(df) < 20: return ""
        try:
            tags = []
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            vol_ma = df['Volume'].rolling(20).mean().iloc[-1]
            spread = latest['High'] - latest['Low']
            prev_spread = prev['High'] - prev['Low']
            
            if latest['Close'] < prev['Close'] and spread < prev_spread and latest['Volume'] < prev['Volume'] and latest['Volume'] < (vol_ma * 0.7):
                tags.append("No Supply")
            if latest['Close'] < prev['Close'] and latest['Volume'] > (vol_ma * 2.0) and latest['Close'] > (latest['Low'] + spread * 0.5):
                tags.append("Stopping Vol")
            return ", ".join(tags) if tags else "Bình thường"
        except: return "Bình thường"

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

        if (tr_bottom * 0.80) <= current_price <= (tr_bottom * tolerance) and latest_vol < (latest_sma * vol_ratio):
            return "Spring (Mua)"
        if (tr_top * 0.95) <= current_price <= (tr_top * 1.05) and latest_vol < (latest_sma * vol_ratio):
            return "Back-up (Mua)"
        if current_price > (tr_top * 0.98) and latest_vol > (latest_sma * 1.5) and latest_close > (latest_high + latest_low) / 2:
            return "SOS Vượt đỉnh (Mua)"
        if latest_high > tr_top and latest_close < (latest_high + latest_low) / 2 and latest_vol > (latest_sma * 1.5):
            return "Upthrust (Bán)"
        return None

# ==========================================
# KHỐI 3: DATABASE & CẢNH BÁO TELEGRAM
# ==========================================
class FirestoreManager:
    def __init__(self, key_path="firebase_key.json"):
        try:
            cred = credentials.Certificate(key_path)
            if not firebase_admin._apps: firebase_admin.initialize_app(cred)
            self.db = firestore.client()
        except: self.db = None

    def push_signal(self, signal_data):
        if not self.db: return
        try: self.db.collection('wyckoff_signals').document().set(signal_data)
        except: pass

def send_telegram_alert(bot_token, chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception: pass

if __name__ == "__main__":
    db_manager = FirestoreManager()
    db = db_manager.db
    if db is None: exit()
        
    try:
        doc = db.collection("system_config").document("watchlist").get()
        my_portfolio = doc.to_dict().get("tickers", []) if doc.exists else ["FPT"]
    except: my_portfolio = ["FPT"]
    
    try:
        param_doc = db.collection("system_config").document("wyckoff_params").get()
        sys_params = param_doc.to_dict() if param_doc.exists else {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}
    except: sys_params = {"vol_ma_period": 20, "sc_vol_multiplier": 2.5, "spring_vol_ratio": 0.5, "spring_price_tolerance": 1.05}
        
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    vsa_engine = WyckoffVSASignal(sys_params)
    
    # Chuẩn bị gửi Telegram nếu được cài đặt
    tele_doc = db.collection("system_config").document("telegram").get()
    tele_config = tele_doc.to_dict() if tele_doc.exists else {}
    bot_token = tele_config.get("bot_token", "")
    chat_id = tele_config.get("chat_id", "")
    alert_messages = []

    for ticker in my_portfolio:
        try:
            time.sleep(0.2)
            fetcher = QuantDataFetcher(ticker)
            df = fetcher.fetch_daily_data(start_date, end_date)
            
            if df is not None and len(df) > 60:
                current_price = float(df['Close'].iloc[-1])
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
                if tr_top is None or tr_bottom is None: continue 
                
                signal_type = vsa_engine.detect_advanced_signals(df, current_price, tr_top, tr_bottom)
                if signal_type:
                    rs_score = round(((current_price - float(df['Close'].iloc[-60])) / float(df['Close'].iloc[-60])) * 100, 2)
                    weekly_trend = vsa_engine.check_weekly_trend(df)
                    vsa_tags = vsa_engine.get_vsa_tags(df)
                    
                    # TÍNH TOÁN CÁC BIẾN SỐ PRO MỚI
                    atr_val = vsa_engine.calculate_atr(df)
                    poc_val = vsa_engine.calculate_poc(df, tr_bottom, tr_top)
                    trailing_stop = round(current_price - (1.5 * atr_val), 2) if atr_val else 0

                    # TÍNH ĐIỂM RATING TOÀN DIỆN (MAX 100)
                    rating = 50 # Điểm gốc
                    if rs_score > 0: rating += min(rs_score, 20) # Tối đa +20 điểm từ RS
                    if weekly_trend == "TĂNG (Uptrend)": rating += 15
                    if "No Supply" in vsa_tags or "Stopping Vol" in vsa_tags: rating += 15
                    rating = int(min(max(rating, 0), 100)) # Chặn trong mức 0-100

                    signal_data = {
                        "Date_Detected": df.index[-1].strftime('%Y-%m-%d'), "Ticker": ticker, "Price": float(current_price),
                        "Signal_Type": signal_type, "TR_Top": float(tr_top), "TR_Bottom": float(tr_bottom), "RS_Score": rs_score,
                        "Weekly_Trend": weekly_trend, "VSA_Tags": vsa_tags,
                        "Rating_Score": rating, "Trailing_Stop": trailing_stop, "POC_Level": poc_val,
                        "Status": "Mới phát hiện", "Timestamp": firestore.SERVER_TIMESTAMP
                    }
                    db_manager.push_signal(signal_data)

                    # Lưu vào danh sách cảnh báo
                    if "Mua" in signal_type and rating >= 70:
                        alert_messages.append(f"🟢 <b>{ticker}</b> ({signal_type})\nGiá: {current_price} | Điểm: {rating}/100\nCắt lỗ ATR: {trailing_stop}")
        except: pass

    # Bắn Cảnh Báo Telegram
    if bot_token and chat_id and alert_messages:
        final_msg = "🚀 <b>WYCKOFF RADAR PRO PHÁT HIỆN</b>\n\n" + "\n\n".join(alert_messages)
        send_telegram_alert(bot_token, chat_id, final_msg)
