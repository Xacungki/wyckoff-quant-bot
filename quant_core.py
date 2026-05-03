import yfinance as yf
import pandas as pd
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# --- KHỐI 1: LẤY DỮ LIỆU (Giữ nguyên) ---
class QuantDataFetcher:
    def __init__(self, ticker):
        self.ticker = ticker

    def fetch_daily_data(self, start_date, end_date):
        df = yf.download(self.ticker, start=start_date, end=end_date, interval="1d", progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

# --- KHỐI 2: LOGIC WYCKOFF (Giữ nguyên) ---
class WyckoffVSASignal:
    def __init__(self, volume_ma_period=20):
        self.volume_ma_period = volume_ma_period

    def detect_supply_exhaustion(self, df, current_price):
        support_level = current_price * 0.95 
        df['Vol_SMA'] = df['Volume'].rolling(window=self.volume_ma_period).mean()
        condition_near_support = (df['Close'] >= support_level) & (df['Close'] <= current_price * 1.05)
        condition_low_volume = df['Volume'] < (df['Vol_SMA'] * 0.6)
        df['Is_Exhaustion_Signal'] = condition_near_support & condition_low_volume
        return df[df['Is_Exhaustion_Signal'] == True]

    def identify_trading_range(self, df, sys_params):
        if len(df) < 50:
            return None, None
            
        ma_period = sys_params.get("vol_ma_period", 20)
        sc_mult = sys_params.get("sc_vol_multiplier", 2.5)
        
        df['Vol_MA'] = df['Volume'].rolling(window=ma_period).mean()
        df['Is_SC'] = (df['Volume'] > df['Vol_MA'] * sc_mult) & (df['Close'] < df['Open'])
        
        recent_data = df.tail(60)
        sc_candles = recent_data[recent_data['Is_SC'] == True]
        
        # ... (Phần logic tìm đáy và đỉnh của bạn ở dưới giữ nguyên) ...
            
        df['Vol_MA_20'] = df['Volume'].rolling(window=20).mean()
        df['Is_SC'] = (df['Volume'] > df['Vol_MA_20'] * 2.5) & (df['Close'] < df['Open'])
        
        recent_data = df.tail(60)
        sc_candles = recent_data[recent_data['Is_SC'] == True]
        
        if sc_candles.empty:
            return None, None
            
        sc_date = sc_candles['Volume'].idxmax()
        sc_index = df.index.get_loc(sc_date)
        
        tr_bottom = df['Low'].iloc[sc_index:sc_index+3].min()
        
        if sc_index + 15 < len(df):
            tr_top = df['High'].iloc[sc_index+1:sc_index+16].max()
        else:
            tr_top = df['High'].iloc[sc_index+1:].max()
            
        return tr_top, tr_bottom

# ==========================================
# KHỐI 3 (MỚI): QUẢN TRỊ CƠ SỞ DỮ LIỆU ĐÁM MÂY
# ==========================================
class FirestoreManager:
    def __init__(self, key_path="firebase_key.json"):
        """Khởi tạo kết nối bảo mật với Google Cloud Firestore"""
        try:
            cred = credentials.Certificate(key_path)
            # Kiểm tra xem app đã khởi tạo chưa để tránh lỗi chạy nhiều lần
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("[+] Đã kết nối thành công tới Database Đám mây!")
        except Exception as e:
            print(f"[!] Lỗi kết nối Database: {e}")
            self.db = None

    def push_signal(self, signal_data):
        """Bắn 1 gói dữ liệu tín hiệu lên bộ sưu tập 'vsa_signals'"""
        if not self.db: return
        
        try:
            # Tạo một document mới với ID tự động sinh
            doc_ref = self.db.collection('vsa_signals').document()
            doc_ref.set(signal_data)
        except Exception as e:
            print(f"Lỗi khi đẩy dữ liệu mã {signal_data.get('Ticker')}: {e}")

# ==========================================
# KHỐI 4: TRẠM ĐIỀU PHỐI TỔNG THỂ (Đã nâng cấp)
# ==========================================
class MarketScanner:
    def __init__(self, watchlist):
        self.watchlist = watchlist
        self.vsa_logic = WyckoffVSASignal()
        self.db_manager = FirestoreManager() # Gọi Khối Database vào làm việc
        self.report = []

    def run_daily_scan(self, start_date, end_date):
        print(f"\n🚀 BẮT ĐẦU QUÉT HỆ THỐNG: {len(self.watchlist)} MÃ CỔ PHIẾU...")
        
        for ticker in self.watchlist:
            print(f"  -> Đang phân tích: {ticker}")
            fetcher = QuantDataFetcher(ticker)
            df = fetcher.fetch_daily_data(start_date, end_date)
            
            if df is not None and not df.empty:
                current_price = float(df['Close'].iloc[-1])
                signals = self.vsa_logic.detect_supply_exhaustion(df, current_price)
                
                if not signals.empty:
                    latest_signal = signals.iloc[-1]
                    
                    # 1. Đóng gói dữ liệu chuẩn bị gửi lên mây
                    signal_package = {
                        "Ticker": ticker,
                        "Price": round(float(latest_signal['Close']), 2),
                        "Date_Detected": str(signals.index[-1].strftime('%Y-%m-%d')),
                        "Signal_Type": "Cạn cung (Spring)",
                        "Status": "Mới phát hiện",
                        "Timestamp": firestore.SERVER_TIMESTAMP # Lưu thời gian thực tế đẩy lên
                    }
                    
                    # 2. Đẩy lên Firestore
                    self.db_manager.push_signal(signal_package)
                    
                    # 3. Lưu vào báo cáo tạm thời để in ra màn hình
                    self.report.append(signal_package)

    def generate_report(self):
        print("\n" + "="*50)
        print("📊 ĐÃ ĐỒNG BỘ LÊN ĐÁM MÂY & BÁO CÁO NGÀY:", datetime.now().strftime('%Y-%m-%d'))
        print("="*50)
        if not self.report:
            print("[!] Không phát hiện tín hiệu bất thường nào.")
        else:
            report_df = pd.DataFrame(self.report)
            # Ẩn cột Timestamp khi in ra Terminal cho đỡ rối mắt
            print(report_df.drop(columns=['Timestamp']).to_string(index=False))
        print("="*50 + "\n")


# ==========================================
# KHỐI 5: KÍCH HOẠT HỆ THỐNG
# ==========================================
if __name__ == "__main__":
    print("🚀 Bắt đầu chạy Lõi Quét tự động...")
    
    # Lấy danh sách cấu hình từ Database
    try:
        db = init_firebase() # Đảm bảo bạn đã có hàm này ở trên
        doc_ref = db.collection("system_config").document("watchlist")
        doc = doc_ref.get()
        my_portfolio = doc.to_dict().get("tickers", []) if doc.exists else ["FPT.VN", "VNM.VN", "AAPL"]
    except:
        my_portfolio = ["FPT.VN", "VNM.VN", "AAPL", "NUS"]
        # ... (code lấy my_portfolio cũ giữ nguyên) ...
    
    # --- LẤY BỘ THÔNG SỐ ĐIỀU KHIỂN TỪ FIRESTORE ---
    param_ref = db.collection("system_config").document("wyckoff_params")
    param_doc = param_ref.get()
    if param_doc.exists:
        sys_params = param_doc.to_dict()
    else:
        sys_params = {
            "vol_ma_period": 20, 
            "sc_vol_multiplier": 2.5, 
            "spring_vol_ratio": 0.5, 
            "spring_price_tolerance": 1.05
        }
    print(f"⚙️ Áp dụng thông số cấu hình: {sys_params}")
    # -----------------------------------------------
        
    print(f"📊 Đang tiến hành quét {len(my_portfolio)} mã: {my_portfolio}")
    
    vsa_engine = WyckoffVSASignal() # Khởi tạo bộ máy phân tích
    
    for ticker in my_portfolio:
        try:
            # 1. Kéo dữ liệu
            fetcher = QuantDataFetcher(ticker)
            df = fetcher.fetch_daily_data("2025-01-01", "2026-05-04")
            
            if df is not None and not df.empty:
                current_price = df['Close'].iloc[-1]
                
                # 2. Quét tìm Khung giá (Trading Range)
                tr_top, tr_bottom = vsa_engine.identify_trading_range(df)
                
                # Bỏ qua nếu mã này chưa từng có nhịp bán tháo (chưa có SC)
                if tr_top is None or tr_bottom is None:
                    continue 
                
                # 3. Quét tín hiệu Cạn cung theo logic cũ của bạn
                # (Sửa tên biến is_spring cho phù hợp với cách hàm của bạn trả về kết quả)
                is_spring = vsa_engine.detect_supply_exhaustion(df, current_price) 
                
                # 4. BỘ LỌC KÉP: Có Spring VÀ giá phải nằm ở đáy TR (hoặc đâm thủng nhẹ giả mạo)
                if is_spring and (current_price <= tr_bottom * 1.05):
                    signal_data = {
                        "Date_Detected": df.index[-1].strftime('%Y-%m-%d'),
                        "Ticker": ticker,
                        "Price": float(current_price),
                        "Signal_Type": "Cạn cung (Spring) trong Trading Range",
                        "TR_Top": float(tr_top),
                        "TR_Bottom": float(tr_bottom),
                        "Status": "Mới phát hiện"
                    }
                    print(f"🔥 Phát hiện {ticker} cạn cung tại vùng Đáy của Smart Money!")
                    # Đẩy lên Firestore 
                    db.collection("wyckoff_signals").add(signal_data)
                    
        except Exception as e:
            print(f"Lỗi khi quét {ticker}: {e}")
