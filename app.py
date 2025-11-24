import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Binance Sniper Bot", layout="wide", page_icon="🦅")
st.title("🦅 Binance Sniper Bot: Güvenli Versiyon")

# --- SESSION STATE BAŞLATMA (Hafıza) ---
if 'last_buy_time' not in st.session_state:
    st.session_state['last_buy_time'] = None

# --- YAN PANEL ---
st.sidebar.header("🔑 Binance API Ayarları")
api_key = st.sidebar.text_input("API Key", type="password")
api_secret = st.sidebar.text_input("Secret Key", type="password")
st.sidebar.divider()
st.sidebar.header("⚙️ Strateji Ayarları")
timeframe = st.sidebar.selectbox("Zaman Dilimi", ["1m", "5m", "15m", "1h", "4h"], index=2)
limit = st.sidebar.slider("Analiz Edilecek Mum Sayısı", 50, 500, 100)
symbol_input = st.sidebar.text_input("Coin Sembolü", value="BTC/USDT")
trade_amount_usdt = st.sidebar.number_input("İşlem Başına Tutar ($)", value=12.0, min_value=11.0, help="Binance min limit genelde 10$ olduğu için güvenli olması adına 11-12$ önerilir.")
dry_run = st.sidebar.checkbox("🧪 TEST MODU (Gerçek para harcama)", value=True)

# --- FONKSİYONLAR ---

def init_exchange(api_key, api_secret):
    try:
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        # Piyasaları yükle (Hassasiyet ayarları için gerekli)
        exchange.load_markets()
        return exchange
    except Exception as e:
        st.error(f"Bağlantı Hatası: {e}")
        return None

def fetch_data(exchange, symbol, timeframe, limit):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Time'] = pd.to_datetime(df['Time'], unit='ms')
        return df
    except Exception as e:
        st.error(f"Veri Çekme Hatası: {e}")
        return None

def calculate_indicators(df):
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp12 - exp26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df

def execute_buy_order(exchange, symbol, amount_usdt, current_price):
    try:
        # 1. Alınacak ham miktarı hesapla
        raw_amount = amount_usdt / current_price
        
        # 2. Borsanın kabul edeceği hassasiyete yuvarla (Örn: 0.0012345 -> 0.0012)
        amount_to_buy = exchange.amount_to_precision(symbol, raw_amount)
        
        # 3. Market emri gönder
        order = exchange.create_market_buy_order(symbol, amount_to_buy)
        return order
    except Exception as e:
        return f"HATA: {e}"

# --- ANA KOD ---
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("📡 Kontrol Paneli")
    run_bot = st.checkbox("Botu Başlat", value=False)
    
    if run_bot:
        sleep_time = st.slider("Tarama Aralığı (Saniye)", 10, 120, 30)
        
        if not api_key or not api_secret:
            st.error("API Anahtarları Eksik!")
        else:
            exchange = init_exchange(api_key, api_secret)
            if exchange:
                st.info(f"Son Tarama: {datetime.now().strftime('%H:%M:%S')}")
                
                df = fetch_data(exchange, symbol_input, timeframe, limit)
                
                if df is not None:
                    df = calculate_indicators(df)
                    st.session_state['df_chart'] = df # Grafik için kaydet

                    # Son kapanmış mum (Sinyal için)
                    last_closed_candle = df.iloc[-2]
                    prev_candle = df.iloc[-3]
                    current_price = df['Close'].iloc[-1]
                    
                    # Sinyal Zamanı (Mumun açılış zamanı unique ID gibidir)
                    signal_timestamp = last_closed_candle['Time']

                    # --- ANALİZ ---
                    macd_cross = (prev_candle['MACD'] < prev_candle['Signal']) and (last_closed_candle['MACD'] > last_closed_candle['Signal'])
                    trend_ok = last_closed_candle['Close'] > last_closed_candle['EMA200']
                    
                    st.write(f"💰 Fiyat: **{current_price} $**")
                    st.write(f"📈 Trend (EMA200): {'✅ Pozitif' if trend_ok else '🔻 Negatif'}")
                    st.write(f"📊 MACD Kesişimi: {'✅ Var' if macd_cross else '➖ Yok'}")

                    # --- GÜVENLİ ALIM MANTIĞI ---
                    if macd_cross and trend_ok:
                        # DAHA ÖNCE BU MUMDA ALDIK MI?
                        if st.session_state['last_buy_time'] == signal_timestamp:
                            st.warning("⚠️ Sinyal devam ediyor ancak bu mum için zaten işlem yapıldı. Bekleniyor...")
                        else:
                            st.success("🔥 YENİ ALIM SİNYALİ!")
                            
                            if dry_run:
                                st.warning(f"🧪 TEST MODU: {trade_amount_usdt}$ alım simüle edildi.")
                                # Test modunda da olsa hafızaya atalım ki tekrar uyarı vermesin
                                st.session_state['last_buy_time'] = signal_timestamp
                            else:
                                with st.spinner("Emir Gönderiliyor..."):
                                    res = execute_buy_order(exchange, symbol_input, trade_amount_usdt, current_price)
                                    if isinstance(res, dict):
                                        st.balloons()
                                        st.success(f"ALIM BAŞARILI! {res['amount']} adet alındı.")
                                        # Başarılı işlem sonrası hafızayı güncelle
                                        st.session_state['last_buy_time'] = signal_timestamp
                                    else:
                                        st.error(f"Borsa Hatası: {res}")
                    else:
                        st.info("Sinyal aranıyor...")

        # Bekleme ve Yenileme
        time.sleep(sleep_time)
        st.rerun()

# --- GRAFİK KISMI (Değişiklik Yok) ---
with col2:
    if 'df_chart' in st.session_state:
        df_chart = st.session_state['df_chart']
        fig = go.Figure(data=[go.Candlestick(x=df_chart['Time'], open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name='Fiyat')])
        fig.add_trace(go.Scatter(x=df_chart['Time'], y=df_chart['EMA200'], line=dict(color='orange', width=2), name='EMA 200'))
        fig.update_layout(title=f"{symbol_input} - {timeframe}", template="plotly_dark", height=600)
        st.plotly_chart(fig, use_container_width=True)
