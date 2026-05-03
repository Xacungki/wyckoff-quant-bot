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
    print("🚀 Bắt đầu chạy Lõi Quét tự động từ GitHub Actions...")
    
    # 1. Lấy danh sách cấu hình từ Database
    db = init_firebase() # Gọi hàm kết nối có sẵn
    doc_ref = db.collection("system_config").document("watchlist")
    doc = doc_ref.get()
    
    if doc.exists:
        my_portfolio = doc.to_dict().get("tickers", [])
    else:
        # Fallback an toàn nếu chưa có db
        my_portfolio = ["FPT.VN", "VNM.VN", "AAPL", "NUS"] 
        
    print(f"📊 Đang tiến hành quét {len(my_portfolio)} mã: {my_portfolio}")
    
    # 2. Khởi động Lõi
    scanner = MarketScanner(watchlist=my_portfolio)
    scanner.run_daily_scan(start_date="2025-01-01", end_date="2026-05-04")
    scanner.generate_report()
