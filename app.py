import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import time
import logging
import pytz # Saat dilimi yönetimi için eklendi

# İstanbul saat dilimini tanımlama (UTC+3)
ist_tz = pytz.timezone('Europe/Istanbul')

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="1m Scalper Bot", layout="wide", page_icon="⚡")
st.title("⚡ 1-Minute Scalping Bot (Trend + Pullback)")

# --- CSS İLE UI DÜZENLEME ---
st.markdown("""
    <style>
    .stMetric {
        background-color: #1E1E1E;
        padding: 10px;
        border-radius: 5px;
        border: 1px solid #333;
    }
    </style>
    """, unsafe_allow_html=True)

# --- SESSION STATE ---
def init_session_state():
    defaults = {
        'trades': [],           # Geçmiş işlemler
        'balance': 0.50,      # Simülasyon bakiyesi (USDT) - Başlangıç $0.50 olarak ayarlandı
        'positions': {},        # Açık pozisyonlar
        'logs': []              # Bot logları
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# --- YAN PANEL ---
st.sidebar.header("⚙️ Bot Ayarları")

# Mod Seçimi
mode = st.sidebar.radio("Çalışma Modu", ["🧪 Simülasyon (Paper Trading)", "🚀 Canlı (Binance API)"])

api_key = ""
api_secret = ""

if mode == "🚀 Canlı (Binance API)":
    with st.sidebar.expander("Binance API", expanded=True):
        api_key = st.text_input("API Key", type="password")
        api_secret = st.text_input("Secret Key", type="password")
else:
    # Simülasyon bakiyesi burada gösteriliyor
    st.sidebar.info(f"🧪 Simülasyon Bakiyesi: ${st.session_state['balance']:.2f}")

st.sidebar.divider()

# Strateji Parametreleri
st.sidebar.subheader("Strateji: Trend Pullback")
symbol_list = st.sidebar.multiselect("Coinler", 
                                     ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"],
                                     default=["BTC/USDT", "ETH/USDT"])

timeframe = "1m" # Sabit 1 dakika
limit = 100      # Analiz mum sayısı

col1, col2 = st.sidebar.columns(2)
with col1:
    stop_atr_mult = st.number_input("Stop ATR x", 1.0, 5.0, 2.0, 0.1) # Stop Loss
with col2:
    tp_atr_mult = st.number_input("TP ATR x", 1.0, 10.0, 3.5, 0.1)  # Take Profit

# İşlem büyüklüğü varsayılanı $0.10 olarak ayarlandı
trade_size = st.sidebar.number_input("İşlem Büyüklüğü ($)", 0.01, 1000.0, 0.10) 

# Otomatik Yenileme
auto_run = st.sidebar.checkbox("Botu Çalıştır", value=False)
refresh_rate = st.sidebar.slider("Hız (Saniye)", 5, 60, 10)

# --- FONKSİYONLAR ---

def get_exchange(key, secret, mode):
    """Exchange nesnesini oluştur"""
    # CANLI İŞLEM RİSKİ: Bu fonksiyon borsa bağlantısını sağlar.
    if mode == "🚀 Canlı (Binance API)" and key and secret:
        return ccxt.binance({
            'apiKey': key,
            'secret': secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
    else:
        # Simülasyon için public data çeken dummy exchange
        return ccxt.binance({'enableRateLimit': True})

@st.cache_data(ttl=5)
def fetch_ohlcv(_exchange, symbol, timeframe, limit):
    try:
        bars = _exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Time'] = pd.to_datetime(df['Time'], unit='ms')
        return df
    except Exception as e:
        st.error(f"Veri hatası ({symbol}): {e}")
        return None

def calculate_signals(df):
    if df is None: return None
    
    # 1. EMA 200 (Trend)
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    # 2. RSI 14
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # 3. MACD (12, 26, 9)
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp12 - exp26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    # 4. ATR (Volatilite)
    df['TR'] = pd.concat([
        df['High'] - df['Low'],
        abs(df['High'] - df['Close'].shift()),
        abs(df['Low'] - df['Close'].shift())
    ], axis=1).max(axis=1)
    df['ATR'] = df['TR'].rolling(window=14).mean()
    
    return df

def check_entry_conditions(df):
    """
    STRATEJİ:
    1. Trend: Fiyat > EMA200 (Yükseliş Trendi)
    2. Pullback: RSI < 55 (Aşırı alımda değil, düzeltme ihtimali veya sağlıklı yükseliş)
    3. Momentum: MACD Yukarı kesmiş (Teyit)
    """
    # Son kapanmış mum (-2)
    last = df.iloc[-2]
    prev = df.iloc[-3]
    
    # Trend Kontrolü
    is_uptrend = last['Close'] > last['EMA200']
    
    # RSI Kontrolü (Tepeden almamak için sınır)
    is_rsi_safe = last['RSI'] < 55 and last['RSI'] > 35
    
    # MACD Kesişimi (Alttan yukarı)
    macd_cross_up = (prev['MACD'] < prev['Signal']) and (last['MACD'] > last['Signal'])
    
    condition = is_uptrend and is_rsi_safe and macd_cross_up
    
    # Stop/TP Seviyeleri
    stop_loss = last['Close'] - (last['ATR'] * stop_atr_mult)
    take_profit = last['Close'] + (last['ATR'] * tp_atr_mult)
    
    return condition, stop_loss, take_profit

def execute_trade(symbol, entry_price, sl, tp, mode, exchange=None):
    """İşlemi gerçekleştir (Simülasyon veya Gerçek)"""
    
    # Türkiye saatine göre zaman damgası
    timestamp = datetime.now(ist_tz).strftime("%H:%M:%S")
    
    # Zaten pozisyon var mı?
    if symbol in st.session_state['positions']:
        return
        
    if mode == "🚀 Canlı (Binance API)":
        # !!! RİSK UYARISI: GERÇEK İŞLEM DEVREYE ALINMIŞTIR !!!
        try:
            # İşlem büyüklüğünü (trade_size USDT) coin miktarına çevir
            amount = trade_size / entry_price 
            
            # Gerçek Piyasa Alım Emri (Market Buy)
            order = exchange.create_order(
                symbol=symbol,
                type='market',
                side='buy',
                amount=amount
            )
            
            # Canlı modda pozisyonu takip etmek için state güncelleniyor.
            st.session_state['positions'][symbol] = {
                'entry': order['price'] if 'price' in order and order['price'] is not None else entry_price, # Gerçekleşen fiyat
                'amount': order['filled'], # Gerçekleşen miktar
                'sl': sl,
                'tp': tp,
                'time': timestamp,
                'order_id': order['id']
            }
            log_msg = f"🟢 CANLI ALIM: {symbol} @ {st.session_state['positions'][symbol]['entry']:.4f} | Order ID: {order['id']}"
            st.session_state['logs'].insert(0, log_msg)
            st.toast(log_msg, icon="✅")

        except Exception as e:
            error_msg = f"🔴 CANLI İŞLEM HATASI ({symbol}): {e}"
            st.session_state['logs'].insert(0, error_msg)
            st.toast(error_msg, icon="❌")
            # Hata durumunda simülasyon pozisyonu açılmaz
            pass

    else:
        # Simülasyon
        cost = trade_size
        if st.session_state['balance'] >= cost:
            st.session_state['balance'] -= cost
            st.session_state['positions'][symbol] = {
                'entry': entry_price,
                'amount': cost / entry_price,
                'sl': sl,
                'tp': tp,
                'time': timestamp
            }
            log_msg = f"🔵 ALIM: {symbol} @ {entry_price:.4f} | SL: {sl:.4f} TP: {tp:.4f}"
            st.session_state['logs'].insert(0, log_msg)
            st.toast(log_msg, icon="🚀")

def check_exit_conditions(df, symbol, mode):
    """Açık pozisyonları kontrol et ve çıkış emri gönder"""
    if symbol not in st.session_state['positions']:
        return

    pos = st.session_state['positions'][symbol]
    current_price = df['Close'].iloc[-1] # Anlık fiyatla çıkış kontrolü
    
    reason = None
    pnl = 0
    
    # Stop Loss
    if current_price <= pos['sl']:
        reason = "🛑 STOP LOSS"
        exit_price = pos['sl'] # Simülasyon SL fiyatından çıkar
        
    # Take Profit
    elif current_price >= pos['tp']:
        reason = "✅ TAKE PROFIT"
        exit_price = pos['tp'] # Simülasyon TP fiyatından çıkar
        
    if reason:
        # PNL hesaplama (Hem canlı hem simülasyon için)
        pnl = (exit_price - pos['entry']) * pos['amount']
        
        # Pozisyon kapatma emri
        if mode == "🚀 Canlı (Binance API)":
            # !!! RİSK UYARISI: GERÇEK SATIŞ EMİRİ GÖNDERİLİYOR !!!
            try:
                # Gerçek Piyasa Satış Emri (Market Sell)
                exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side='sell',
                    amount=pos['amount']
                )
                log_msg = f"🟢 CANLI SATIŞ: {symbol} ({reason}) | PNL: Borsa Tarafından Hesaplanacak"
                st.session_state['logs'].insert(0, log_msg)
                st.toast(log_msg, icon="💸")
                # Not: Canlı PNL hesaplaması ve bakiye güncellemesi borsada gerçekleşir.
                
            except Exception as e:
                error_msg = f"🔴 CANLI SATIŞ HATASI ({symbol}): {e}"
                st.session_state['logs'].insert(0, error_msg)
                st.toast(error_msg, icon="❌")
                # Hata durumunda pozisyonu silmiyoruz, manuel müdahale beklenir.
                return
        
        else: # Simülasyon
            st.session_state['balance'] += (trade_size + pnl)
        
        # Simülasyon veya başarılı canlı işlemde pozisyonu kapat
        del st.session_state['positions'][symbol]
        
        # Geçmişe kaydet
        trade_record = {
            'Symbol': symbol,
            'Type': reason,
            'Entry': pos['entry'],
            'Exit': exit_price,
            'PNL ($)': pnl,
            'Time': datetime.now(ist_tz).strftime("%H:%M") # Türkiye saatine göre güncellendi
        }
        st.session_state['trades'].insert(0, trade_record)
        if mode != "🚀 Canlı (Binance API)":
            st.session_state['logs'].insert(0, f"{reason}: {symbol} | PNL: ${pnl:.2f}")


# --- ANA AKIŞ ---

exchange = get_exchange(api_key, api_secret, mode)

# Ana Dashboard Container
dashboard = st.container()
log_container = st.container()

if auto_run:
    with st.spinner('Piyasa taranıyor...'):
        # Her coin için analiz (BU KISIMDA TÜM COINLER KONTROL EDİLİYOR)
        for symbol in symbol_list:
            df = fetch_ohlcv(exchange, symbol, timeframe, limit)
            df = calculate_signals(df)
            
            if df is not None:
                current_price = df['Close'].iloc[-1]
                
                # 1. Çıkış Kontrolü (Varsa)
                check_exit_conditions(df, symbol, mode)
                
                # 2. Giriş Kontrolü
                buy_signal, sl, tp = check_entry_conditions(df)
                
                if buy_signal:
                    execute_trade(symbol, current_price, sl, tp, mode, exchange)
        
        # --- GÖRSELLEŞTİRME ---
        with dashboard:
            # Türkiye saatine göre güncellendi
            st.markdown(f"### 📡 Piyasa Durumu ({datetime.now(ist_tz).strftime('%H:%M:%S')})")
            
            # Metrikler
            m1, m2, m3 = st.columns(3)
            # Bakiye metrik olarak ana ekranda da gösteriliyor
            m1.metric("Bakiye (Simülasyon)", f"${st.session_state['balance']:.2f}")
            m2.metric("Açık Pozisyonlar", len(st.session_state['positions']))
            pnl_total = sum([t['PNL ($)'] for t in st.session_state['trades']])
            m3.metric("Toplam PNL", f"${pnl_total:.2f}", delta_color="normal")
            
            # Açık Pozisyonlar Tablosu
            if st.session_state['positions']:
                st.subheader("Açık İşlemler")
                cols = st.columns(len(st.session_state['positions']))
                for idx, (sym, pos) in enumerate(st.session_state['positions'].items()):
                    # Pozisyonun anlık fiyatını ve PNL'ini hesapla
                    try:
                        curr_price = fetch_ohlcv(exchange, sym, timeframe, 5)['Close'].iloc[-1]
                        unrealized_pnl = (curr_price - pos['entry']) * pos['amount']
                        color = "green" if unrealized_pnl > 0 else "red"
                        
                        with cols[idx]:
                            st.markdown(f"**{sym}**")
                            st.write(f"Giriş: {pos['entry']:.4f}")
                            st.markdown(f"PNL: :{color}[${unrealized_pnl:.2f}]")
                            st.progress((curr_price - pos['sl']) / (pos['tp'] - pos['sl']), text="Hedef Mesafesi")
                    except IndexError:
                        st.warning(f"{sym} için anlık fiyat çekilemiyor.")


            # Grafik (Tüm seçilen coinler için sekmeli gösterim)
            if len(symbol_list) > 0:
                st.subheader("Grafiksel Analiz")
                
                # Seçilen her coin için bir sekme oluşturuluyor
                tabs = st.tabs(symbol_list) 
                
                for i, main_coin in enumerate(symbol_list):
                    with tabs[i]:
                        df_chart = fetch_ohlcv(exchange, main_coin, timeframe, 100)
                        df_chart = calculate_signals(df_chart)
                        
                        if df_chart is not None and not df_chart.empty:
                            fig = go.Figure()
                            # Mum Grafiği (Candlestick)
                            fig.add_trace(go.Candlestick(x=df_chart['Time'], open=df_chart['Open'], high=df_chart['High'],
                                            low=df_chart['Low'], close=df_chart['Close'], name='Fiyat'))
                            # EMA 200 (Trend)
                            fig.add_trace(go.Scatter(x=df_chart['Time'], y=df_chart['EMA200'], line=dict(color='orange'), name='EMA 200'))
                            
                            # Son pozisyonu grafikte göster
                            if main_coin in st.session_state['positions']:
                                pos = st.session_state['positions'][main_coin]
                                fig.add_hline(y=pos['entry'], line_dash="dot", line_color="yellow", annotation_text="Entry")
                                fig.add_hline(y=pos['tp'], line_dash="dash", line_color="green", annotation_text="TP")
                                fig.add_hline(y=pos['sl'], line_dash="dash", line_color="red", annotation_text="SL")

                            fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0), title=f"{main_coin} Analizi", template="plotly_dark")
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.warning(f"{main_coin} için veri çekilemedi veya veri boş.")

        # --- LOGLAR ---
        with log_container:
            st.divider()
            st.subheader("📝 İşlem Geçmişi")
            tab1, tab2 = st.tabs(["Loglar", "İşlem Tablosu"])
            
            with tab1:
                for log in st.session_state['logs'][:10]:
                    st.text(log)
            
            with tab2:
                if st.session_state['trades']:
                    st.dataframe(pd.DataFrame(st.session_state['trades']))
                else:
                    st.info("Henüz kapanmış işlem yok.")

    # Döngü için bekleme (Streamlit native sleep)
    time.sleep(refresh_rate)
    st.rerun()

else:
    st.info("Botu başlatmak için soldaki 'Botu Çalıştır' kutusunu işaretleyin.")
