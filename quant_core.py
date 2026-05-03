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
    from vnstock import stock_historical_data, financial_ratio
    VNSTOCK_AVAILABLE = True
except ImportError:
    VNSTOCK_AVAILABLE = False

import yfinance as yf

# ==========================================
# KHỐI 1: LẤY DỮ LIỆU (DAILY & INTRADAY)
# ==========================================
class QuantDataFetcher:
    def __init__(self, ticker):
        self.original_ticker = str(ticker).upper().strip()
        self.base_ticker = self.original_ticker.replace(".VN", "").replace(".HM", "").replace(".HN", "").replace(".UP", "")

    def _process_vnstock_df(self, df):
        if df is not None and not df.empty and len(df) > 10:
            df = df.rename(columns={'time': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
            df['Date'] = pd.to_datetime(df['Date'])
            df.set_index('Date', inplace=True)
            clean_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            clean_df = clean_df[clean_df['Volume'] > 0]
            if len(clean_df) > 20:
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    clean_df[col] = pd.to_numeric(clean_df[col], errors='coerce')
                return clean_df
        return None

    def fetch_daily_data(self, start_date, end_date):
        if VNSTOCK_AVAILABLE:
            try:
                df = stock_historical_data(symbol=self.base_ticker, start_date=start_date, end_date=end_date, resolution='1D', type='stock')
                clean_df = self._process_vnstock_df(df)
                if clean_df is not None: return clean_df
            except Exception: pass 

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
                        if len(clean_df) > 20: return clean_df
            except Exception: continue
        return None

    # TÍNH NĂNG TỐI THƯỢNG 4: LẤY DỮ LIỆU REAL-TIME TRONG PHIÊN (15 PHÚT)
    def fetch_intraday_data(self, start_date, end_date):
        if VNSTOCK_AVAILABLE:
            try:
                df = stock_historical_data(symbol=self.base_ticker, start_date=start_date, end_date=end_date, resolution='15', type='stock')
                clean_df = self._process_vnstock_df(df)
                if clean_df is not None: return clean_df
            except Exception: pass
        return None

# ==========================================
# KHỐI 2: BỘ LỌC CƠ BẢN & ĐỘ RỘNG THỊ TRƯỜNG
# ==========================================
class MarketAnalyzer:
    # TÍNH NĂNG TỐI THƯỢNG 1: BỘ LỌC CƠ BẢN (TRÁNH HÀNG RÁC)
    @staticmethod
    def is_fundamentally_good(ticker):
        if not VNSTOCK_AVAILABLE: return True # Bỏ qua nếu không có vnstock
        try:
            base_ticker = str(ticker).upper().replace(".VN", "").strip()
            # Lấy chỉ số tài chính cơ bản
            fr = financial_ratio(base_ticker, 'yearly', True)
            if fr is not None and not fr.empty:
                roe = fr.get('roe', pd.Series([15])).iloc[0]
                # Nếu ROE < 5% (Làm ăn kém hiệu quả), đánh dấu là hàng Rác
                if roe < 0.05: return False
        except Exception: pass
        return True # Mặc định cho qua nếu lỗi API để không block hệ thống

    # TÍNH NĂNG TỐI THƯỢNG 2: CHỈ BÁO ĐỘ RỘNG THỊ TRƯỜNG (MARKET BREADTH)
    @staticmethod
    def calculate_market_breadth(watchlist):
        above_ma50 = 0
        valid_tickers = 0
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
        
        # Quét ngẫu nhiên tối đa 20 mã đại diện để lấy nhiệt kế thị trường nhanh
        sample_list = watchlist[:20] if len(watchlist) > 20 else watchlist
        
        for ticker in sample_list:
            df = QuantDataFetcher(ticker).fetch_daily_data(start_date, end_date)
            if df is not None and len(df) > 50:
                ma50 = df['Close'].rolling(50).mean().iloc[-1]
                if df['Close'].iloc[-1] > ma50:
                    above_ma50 += 1
                valid_tickers += 1
                
        if valid_tickers == 0: return 50.0
        breadth_pct = round((above_ma50 / valid_tickers) * 100, 2)
        return breadth_pct

# ==========================================
# KHỐI 3: LOGIC WYCKOFF VSA
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
            
        sc_index = df.index.get_loc(sc_candles['Volume'].idxmax())
        tr_bottom = df['Low'].iloc[sc_index:min(sc_index+4, len(df))].min()
        tr_top = df['High'].iloc[sc_index+1:sc_index+16].max() if sc_index + 15 < len(df) else df['High'].iloc[sc_index+1:].max()
        return float(tr_top), float(tr_bottom)

    def calculate_poc(self, df, tr_bottom, tr_top):
        try:
            tr_data = df[(df['Close'] >= tr_bottom * 0.95) & (df['Close'] <= tr_top * 1.05)].tail(100)
            if tr_data.empty: return None
            bins = np.linspace(tr_bottom * 0.95, tr_top * 1.05, 20)
            tr_data['Price_Bin'] = pd.cut(tr_data['Close'], bins=bins)
            return round(float(tr_data.groupby('Price_Bin')['Volume'].sum().idxmax().mid), 2)
        except Exception: return None

    def calculate_atr(self, df, period=14):
        try:
            true_range = np.max([df['High'] - df['Low'], np.abs(df['High'] - df['Close'].shift()), np.abs(df['Low'] - df['Close'].shift())], axis=0)
            return round(float(pd.Series(true_range).rolling(period).mean().iloc[-1]), 2)
        except Exception: return 0

    def check_weekly_trend(self, df):
        if len(df) < 100: return "Không rõ"
        try:
            weekly_df = df.resample('W-FRI').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'}).dropna()
            weekly_df['MA30'] = weekly_df['Close'].rolling(30).mean()
            return "TĂNG (Uptrend)" if len(weekly_df) > 30 and weekly_df['Close'].iloc[-1] > weekly_df['MA30'].iloc[-1] else "GIẢM (Downtrend)"
        except: return "Không rõ"

    def get_vsa_tags(self, df):
        if len(df) < 20: return ""
        try:
            tags = []
            latest, prev = df.iloc[-1], df.iloc[-2]
            vol_ma = df['Volume'].rolling(20).mean().iloc[-1]
            if latest['Close'] < prev['Close'] and (latest['High'] - latest['Low']) < (prev['High'] - prev['Low']) and latest['Volume'] < (vol_ma * 0.7):
                tags.append("No Supply")
            if latest['Close'] < prev['Close'] and latest['Volume'] > (vol_ma * 2.0) and latest['Close'] > (latest['Low'] + (latest['High'] - latest['Low']) * 0.5):
                tags.append("Stopping Vol")
            return ", ".join(tags) if tags else "Bình thường"
        except: return "Bình thường"

    def detect_advanced_signals(self, df, current_price, tr_top, tr_bottom, is_intraday=False):
        # Mở khóa thanh khoản nếu là biểu đồ Real-time (Intraday)
        vol_avg_20 = df['Volume'].rolling(20).mean().iloc[-1]
        if not is_intraday and vol_avg_20 < 100000: return None

        ma_period = self.params.get("vol_ma_period", 20)
        vol_ratio = self.params.get("spring_vol_ratio", 0.5)
        tolerance = self.params.get("spring_price_tolerance", 1.05)
        
        df['Vol_SMA'] = df['Volume'].rolling(window=ma_period).mean()
        latest_vol, latest_sma = float(df['Volume'].iloc[-1]), float(df['Vol_SMA'].iloc[-1])
        latest_high, latest_low, latest_close = float(df['High'].iloc[-1]), float(df['Low'].iloc[-1]), float(df['Close'].iloc[-1])

        if (tr_bottom * 0.80) <= current_price <= (tr_bottom * tolerance) and latest_vol < (latest_sma * vol_ratio): return "Spring (Mua)"
        if (tr_top * 0.95) <= current_price <= (tr_top * 1.05) and latest_vol < (latest_sma * vol_ratio): return "Back-up (Mua)"
        if current_price > (tr_top * 0.98) and latest_vol > (latest_sma * 1.5) and latest_close > (latest_high + latest_low) / 2: return "SOS Vượt đỉnh (Mua)"
        if latest_high > tr_top and latest_close < (latest_high + latest_low) / 2 and latest_vol > (latest_sma * 1.5): return "Upthrust (Bán)"
        return None

# ==========================================
# KHỐI 4: CỖ MÁY BACKTEST LỊCH SỬ (TIME MACHINE)
# ==========================================
class Backtester:
    def __init__(self, sys_params):
        self.vsa_engine = WyckoffVSASignal(sys_params)

    def run_backtest(self, df):
        trades = []
        in_trade = False
        entry_price = sl = tp1 = tp2 = 0
        
        # Bắt đầu quét lùi từng ngày từ phiên thứ 100 đến hiện tại
        for i in range(100, len(df)):
            current_slice = df.iloc[:i].copy()
            current_price = current_slice['Close'].iloc[-1]
            current_date = current_slice.index[-1]
            
            if not in_trade:
                tr_top, tr_bottom = self.vsa_engine.identify_trading_range(current_slice)
                if tr_top and tr_bottom:
                    sig = self.vsa_engine.detect_advanced_signals(current_slice, current_price, tr_top, tr_bottom)
                    if sig and "Mua" in sig:
                        in_trade = True
                        entry_price = current_price
                        atr = self.vsa_engine.calculate_atr(current_slice)
                        sl = current_price - (1.5 * atr) if atr else current_price * 0.95
                        tp1 = current_price + (tr_top - current_price) * 0.5
                        tp2 = tr_top
                        trades.append({
                            'Entry_Date': current_date.strftime('%Y-%m-%d'), 'Entry_Price': entry_price, 
                            'Signal': sig, 'SL': sl, 'TP1': tp1, 'TP2': tp2
                        })
            else:
                # Kịch bản 1: Chạm Cắt Lỗ (SL)
                if current_price <= sl:
                    trades[-1]['Exit_Date'] = current_date.strftime('%Y-%m-%d')
                    trades[-1]['Exit_Price'] = current_price
                    trades[-1]['PnL_Pct'] = round(((current_price - entry_price) / entry_price) * 100, 2)
                    trades[-1]['Reason'] = '🛑 Cắt Lỗ'
                    in_trade = False
                # Kịch bản 2: Chạm Chốt Lời Cuối (TP2)
                elif current_price >= tp2:
                    trades[-1]['Exit_Date'] = current_date.strftime('%Y-%m-%d')
                    trades[-1]['Exit_Price'] = current_price
                    trades[-1]['PnL_Pct'] = round(((current_price - entry_price) / entry_price) * 100, 2)
                    trades[-1]['Reason'] = '🎯 Chốt Lời'
                    in_trade = False

        # Nếu lệnh cuối chưa đóng, chốt bằng giá hiện tại
        if in_trade and len(trades) > 0:
            trades[-1]['Exit_Date'] = df.index[-1].strftime('%Y-%m-%d')
            trades[-1]['Exit_Price'] = df['Close'].iloc[-1]
            trades[-1]['PnL_Pct'] = round(((df['Close'].iloc[-1] - entry_price) / entry_price) * 100, 2)
            trades[-1]['Reason'] = '⏳ Đang Giữ'

        return pd.DataFrame(trades) if trades else pd.DataFrame()

# ==========================================
# KHỐI 5: DATABASE & CẢNH BÁO
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
