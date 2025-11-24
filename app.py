import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Binance Sniper Bot", layout="wide", page_icon="🦅")
st.title("🦅 Binance Sniper Bot: Otomatik Al/Sat")

# --- UYARI ---
st.warning("⚠️ DİKKAT: Bu yazılım gerçek para ile işlem yapabilir. API anahtarlarınızı güvenli tutun. Test Modu kapalıyken yapılan işlemler geri alınamaz.")

# --- YAN PANEL (AYARLAR) ---
st.sidebar.header("🔑 Binance API Ayarları")
api_key = st.sidebar.text_input("API Key", type="password")
api_secret = st.sidebar.text_input("Secret Key", type="password")

st.sidebar.divider()

st.sidebar.header("⚙️ Strateji Ayarları")
# Zaman Dilimi Seçimi
timeframe = st.sidebar.selectbox("Zaman Dilimi", ["1m", "5m", "15m", "1h", "4h"], index=2)
limit = st.sidebar.slider("Analiz Edilecek Mum Sayısı", 50, 500, 100)

# Coin Listesi (USDT Pariteleri)
symbol_input = st.sidebar.text_input("Coin Sembolü (Örn: BTC/USDT)", value="BTC/USDT")
trade_amount_usdt = st.sidebar.number_input("İşlem Başına Tutar ($)", value=15.0, min_value=10.0)

# Güvenlik Kilidi
dry_run = st.sidebar.checkbox("🧪 TEST MODU (Gerçek işlem yapma)", value=True)

# --- FONKSİYONLAR ---

def init_exchange(api_key, api_secret):
    """Binance bağlantısını kurar"""
    try:
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'} # Spot piyasa
        })
        return exchange
    except Exception as e:
        st.error(f"Bağlantı Hatası: {e}")
        return None

def fetch_data(exchange, symbol, timeframe, limit):
    """Binance'den canlı mum verisi çeker"""
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Time'] = pd.to_datetime(df['Time'], unit='ms')
        return df
    except Exception as e:
        st.error(f"Veri Çekme Hatası ({symbol}): {e}")
        return None

def calculate_indicators(df):
    """MACD ve EMA Hesaplar"""
    # EMA 200
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    # MACD
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp12 - exp26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    return df

def execute_buy_order(exchange, symbol, amount_usdt, current_price):
    """Binance'e GERÇEK ALIM emri gönderir"""
    try:
        # Kaç adet coin alınacağını hesapla (Örn: 20$ / 0.5$ = 40 ADA)
        amount_to_buy = amount_usdt / current_price
        
        # Binance hassasiyet ayarları gerekebilir ama basitçe Market emri atıyoruz
        # Not: Spot piyasada bazı coinlerin min alım limiti vardır (genelde 10$)
        order = exchange.create_market_buy_order(symbol, amount_to_buy)
        return order
    except Exception as e:
        return f"HATA: {e}"

# --- ARAYÜZ ---

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("📡 Sinyal Durumu")
    
    if st.button("Analiz Et ve İşlem Yap"):
        if not api_key or not api_secret:
            st.error("Lütfen önce API Anahtarlarını girin!")
        else:
            exchange = init_exchange(api_key, api_secret)
            st.info(f"{symbol_input} için {timeframe} grafiği taranıyor...")
            
            df = fetch_data(exchange, symbol_input, timeframe, limit)
            
            if df is not None:
                df = calculate_indicators(df)
                
                # Son kapanmış mumu ve ondan öncekini al (Canlı mum değiştiği için kapanmışa bakılır)
                last_candle = df.iloc[-2] 
                prev_candle = df.iloc[-3]
                current_price = df['Close'].iloc[-1]
                
                # --- STRATEJİ ---
                # 1. MACD Kesişimi (Yukarı)
                macd_cross = (prev_candle['MACD'] < prev_candle['Signal']) and (last_candle['MACD'] > last_candle['Signal'])
                # 2. Trend (Fiyat > EMA200)
                trend_ok = last_candle['Close'] > last_candle['EMA200']
                
                st.write(f"Anlık Fiyat: **{current_price} $**")
                st.write(f"Trend Durumu (EMA 200): {'✅ Yükseliş' if trend_ok else '🔻 Düşüş'}")
                st.write(f"MACD Sinyali: {'✅ AL' if macd_cross else '➖ Nötr'}")
                
                # --- KARAR MEKANİZMASI ---
                if macd_cross and trend_ok:
                    st.success("🔥 ALIM SİNYALİ TESPİT EDİLDİ!")
                    
                    if dry_run:
                        st.warning(f"🧪 TEST MODU: Gerçek alım yapılmadı. Alınacak miktar: {trade_amount_usdt}$")
                    else:
                        with st.spinner("Gerçek emir Binance'e iletiliyor..."):
                            order_result = execute_buy_order(exchange, symbol_input, trade_amount_usdt, current_price)
                            
                            if isinstance(order_result, dict):
                                st.balloons()
                                st.success(f"İŞLEM BAŞARILI! ID: {order_result['id']}")
                                st.json(order_result)
                            else:
                                st.error(f"İşlem Başarısız: {order_result}")
                else:
                    st.info("Henüz uygun alım fırsatı yok.")
                
                # Grafik için veriyi session state'e atalım
                st.session_state['df_chart'] = df

with col2:
    st.subheader("Grafik Analizi")
    if 'df_chart' in st.session_state:
        df_chart = st.session_state['df_chart']
        
        fig = go.Figure()
        
        # Mumlar
        fig.add_trace(go.Candlestick(
            x=df_chart['Time'],
            open=df_chart['Open'], high=df_chart['High'],
            low=df_chart['Low'], close=df_chart['Close'],
            name='Fiyat'
        ))
        
        # EMA 200
        fig.add_trace(go.Scatter(
            x=df_chart['Time'], y=df_chart['EMA200'],
            line=dict(color='orange', width=2), name='EMA 200'
        ))
        
        fig.update_layout(title=f"{symbol_input} - {timeframe}", template="plotly_dark", height=600)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.write("Veri görüntülemek için sol taraftan analiz başlatın.")
