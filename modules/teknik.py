import ta
import pandas as pd
import numpy as np
import warnings
import time
import requests

warnings.filterwarnings('ignore')

_makro_cache = {"deger": 0.5, "zaman": 0.0, "spx_korelasyon": 0.0}

def korku_acgozluluk_getir():
    try:
        url = "https://api.alternative.me/fng/"
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data["data"][0]["value"]) / 100.0
    except:
        return 0.5

def emir_defteri_analizi(sembol):
    if not sembol.endswith("USDT"): return 1.0
    try:
        url = f"https://api.binance.com/api/v3/depth?symbol={sembol}&limit=100"
        response = requests.get(url, timeout=5)
        data = response.json()
        
        bekleyen_alis = sum([float(bid[0]) * float(bid[1]) for bid in data["bids"]])
        bekleyen_satis = sum([float(ask[0]) * float(ask[1]) for ask in data["asks"]])
        
        if bekleyen_satis == 0 or bekleyen_alis == 0: return 1.0
        
        basinc_orani = bekleyen_alis / bekleyen_satis
        if basinc_orani > 5.0: return 5.0
        if basinc_orani < 0.2: return 0.2
        return round(basinc_orani, 3)
    except:
        return 1.0 

def makro_risk_analizi(sembol_df=None):
    global _makro_cache
    import urllib.request
    import ssl
    import json

    if time.time() - _makro_cache["zaman"] < 3600:
        return _makro_cache["deger"], _makro_cache.get("spx_korelasyon", 0.0)

    try:
        # 1. DXY Analizi
        url_dxy = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=1mo&interval=1d"
        url_spx = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1mo&interval=1d"
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        def yahoo_cek(url):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                data = json.loads(response.read())
                return pd.Series(data['chart']['result'][0]['indicators']['quote'][0]['close']).dropna()

        dxy_kapanislar = yahoo_cek(url_dxy)
        spx_kapanislar = yahoo_cek(url_spx)
        
        # DXY Skoru
        son_dxy = dxy_kapanislar.iloc[-1]
        ma20_dxy = dxy_kapanislar.rolling(20).mean().iloc[-1]
        dxy_skor = 0.5
        if son_dxy > ma20_dxy: dxy_skor -= 0.2
        else: dxy_skor += 0.2
        
        # SPX Korelasyonu (Basit eğilim karşılaştırması)
        spx_getiri = spx_kapanislar.pct_change().iloc[-1]
        spx_kor = 1.0 if spx_getiri > 0 else -1.0
        
        _makro_cache["deger"] = max(0.0, min(1.0, float(dxy_skor)))
        _makro_cache["spx_korelasyon"] = spx_kor
        _makro_cache["zaman"] = time.time()
        
        return _makro_cache["deger"], _makro_cache["spx_korelasyon"]
    except:
        return _makro_cache["deger"], _makro_cache.get("spx_korelasyon", 0.0)

def balina_emirleri_cek(sembol):
    """Binance üzerinden son 1 dakikalık büyük emirleri ($100k+) çeker."""
    try:
        url = f"https://api.binance.com/api/v3/aggTrades?symbol={sembol}&limit=500"
        response = requests.get(url, timeout=5)
        trades = response.json()
        
        esik = 100000 # $100,000
        buy_whale = 0
        sell_whale = 0
        
        for t in trades:
            usd_deger = float(t['p']) * float(t['q'])
            if usd_deger >= esik:
                if t['m']: # m=True ise Maker Satış (yani Satış baskısı)
                    sell_whale += usd_deger
                else:
                    buy_whale += usd_deger
                    
        toplam = buy_whale + sell_whale + 1e-9
        # Normalize: 0.5 nötr, 1.0 full alım, 0.0 full satış
        return round(buy_whale / toplam, 2)
    except:
        return 0.5

def volume_profile_poc(df, period=200):
    """Point of Control (POC) hesaplar."""
    d = df.iloc[-period:].copy()
    if len(d) < period: return d["close"].iloc[-1]
    
    bins = 50
    price_min = d["low"].min()
    price_max = d["high"].max()
    bin_size = (price_max - price_min) / bins
    
    volumes = {}
    for i in range(bins):
        low = price_min + i * bin_size
        high = low + bin_size
        mask = (d["close"] >= low) & (d["close"] < high)
        volumes[low + bin_size/2] = d.loc[mask, "volume"].sum()
        
    return max(volumes, key=volumes.get)

def order_block_bul(df, period=100):
    """Basit Order Block tespiti."""
    d = df.iloc[-period:].copy()
    # Son 5 mumda güçlü bir kırılım (engulfing + hacim) ara
    for i in range(len(d)-2, len(d)-7, -1):
        prev = d.iloc[i-1]
        curr = d.iloc[i]
        
        # Bullish OB: Kırmızı mumdan sonra gelen güçlü yeşil mum
        if curr["close"] > prev["high"] and curr["volume"] > d["volume"].mean() * 1.5:
            return prev["low"], 1 # Bullish OB (Destek)
            
        # Bearish OB: Yeşil mumdan sonra gelen güçlü kırmızı mum
        if curr["close"] < prev["low"] and curr["volume"] > d["volume"].mean() * 1.5:
            return prev["high"], -1 # Bearish OB (Direnç)
            
    return 0, 0


# ============================================================
#  FUNDING RATE — Binance Futures API
# ============================================================
_funding_cache = {}

def funding_rate_getir(sembol):
    """Binance Futures'tan son funding rate'i çeker.
    Pozitif = long'cular ödüyor (kalabalık long), Negatif = short'cular ödüyor."""
    global _funding_cache
    
    # 15 dakikada bir güncelle (funding rate 8 saatte bir değişir)
    cached = _funding_cache.get(sembol)
    if cached and (time.time() - cached["zaman"]) < 900:
        return cached["deger"]
    
    try:
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sembol}&limit=1"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and len(data) > 0:
            rate = float(data[0]["fundingRate"])
            # Normalize: tipik aralık -0.01 ~ +0.01, bunu -1 ~ +1'e map'le
            normalized = max(-1.0, min(1.0, rate * 100))
            _funding_cache[sembol] = {"deger": normalized, "zaman": time.time()}
            return normalized
    except:
        pass
    
    _funding_cache[sembol] = {"deger": 0.0, "zaman": time.time()}
    return 0.0


# ============================================================
#  8 KRİPTO-OPTİMİZE İNDİKATÖR — YALIN & GÜÇLÜ
# ============================================================
def indiktor_hesapla(data):
    d = data.copy()

    # ─── 1. HAREKETLİ ORTALAMALAR (EMA) ───
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["ema200"] = d["close"].ewm(span=200, adjust=False).mean()
    
    # Fiyatın ortalamalara uzaklığı (Model için normalize özellikler)
    d["ema20_sapma"] = (d["close"] - d["ema20"]) / (d["ema20"] + 1e-9)
    d["ema50_sapma"] = (d["close"] - d["ema50"]) / (d["ema50"] + 1e-9)
    d["ema200_sapma"] = (d["close"] - d["ema200"]) / (d["ema200"] + 1e-9)

    # ─── 2. MACD (Trend & Momentum) ───
    macd_ind = ta.trend.MACD(d["close"], window_slow=26, window_fast=12, window_sign=9)
    d["macd"] = macd_ind.macd()
    d["macd_signal"] = macd_ind.macd_signal()
    d["macd_hist"] = macd_ind.macd_diff()

    # ─── 3. VOLATİLİTE (Bollinger Bands & ATR) ───
    bb = ta.volatility.BollingerBands(d["close"], window=20, window_dev=2)
    d["bb_upper"] = bb.bollinger_hband()
    d["bb_lower"] = bb.bollinger_lband()
    d["bb_mid"] = bb.bollinger_mavg()
    d["bb_pband"] = bb.bollinger_pband() # Fiyatın banttaki yeri (0-1)
    d["bb_wband"] = bb.bollinger_wband() # Bant genişliği (volatilite)

    d["atr"] = ta.volatility.AverageTrueRange(d["high"], d["low"], d["close"], window=14).average_true_range()
    d["atr_ratio"] = d["atr"] / (d["close"] + 1e-9)

    # ─── 4. MOMENTUM (RSI & Stochastic RSI) ───
    d["rsi"] = ta.momentum.RSIIndicator(d["close"], window=14).rsi()
    stoch_rsi = ta.momentum.StochRSIIndicator(d["close"], window=14, smooth1=3, smooth2=3)
    d["stoch_rsi_k"] = stoch_rsi.stochrsi_k()
    d["stoch_rsi_d"] = stoch_rsi.stochrsi_d()

    # ─── 5. HACIM (OBV & VWAP & Volume Ratio) ───
    d["obv"] = ta.volume.OnBalanceVolumeIndicator(d["close"], d["volume"]).on_balance_volume()
    # OBV'nin eğimi (son 5 mum) model için daha anlamlı
    d["obv_degisim"] = d["obv"].diff(5) / (d["volume"].rolling(20).mean() * 5 + 1e-9)

    cumvol = d["volume"].cumsum()
    cumvp = (d["close"] * d["volume"]).cumsum()
    d["vwap"] = cumvp / (cumvol + 1e-9)
    d["vwap_sapma"] = (d["close"] - d["vwap"]) / (d["vwap"] + 1e-9)
    
    d["volume_ratio"] = d["volume"] / (d["volume"].rolling(20).mean() + 1e-9)

    # ─── 5.1. SQUEEZE (Bollinger / Keltner Squeeze) ───
    # Volatilite daralmasını (fırtına sessizliği) bulmak için
    d["sqz_on"] = (d["bb_lower"] > (d["bb_mid"] - 2 * d["atr"])) & (d["bb_upper"] < (d["bb_mid"] + 2 * d["atr"]))
    d["squeeze_aktif"] = d["sqz_on"].astype(int)

    # ─── 6. DESTEK / DİRENÇ (Fibonacci Retracement) ───
    # Son 100 mumun en yüksek ve en düşük değerlerini baz alalım
    period = 100
    roll_max = d["high"].rolling(window=period).max()
    roll_min = d["low"].rolling(window=period).min()
    diff = roll_max - roll_min
    
    d["fib_0"] = roll_min
    d["fib_0.236"] = roll_min + 0.236 * diff
    d["fib_0.382"] = roll_min + 0.382 * diff
    d["fib_0.5"] = roll_min + 0.5 * diff
    d["fib_0.618"] = roll_min + 0.618 * diff
    d["fib_0.786"] = roll_min + 0.786 * diff
    d["fib_1"] = roll_max
    
    # Fiyatın Fibonacci seviyesine göre konumu (-1 ile +1 arası normalize)
    d["fib_skor"] = (d["close"] - d["fib_0.5"]) / (diff / 2 + 1e-9)

    # ─── 7. EKSTRA GÜÇLÜ SİNYALLER (Trend Takipçileri) ───
    # Supertrend (Mevcut logic korunuyor)
    atr_period = 10
    atr_mult = 3.0
    hl2 = (d["high"] + d["low"]) / 2
    st_atr = ta.volatility.AverageTrueRange(d["high"], d["low"], d["close"], window=atr_period).average_true_range()
    
    st_upper = hl2 + (atr_mult * st_atr)
    st_lower = hl2 - (atr_mult * st_atr)
    
    # Squeeze Momentum (Mevcut logic korunuyor)
    keltner_ma = d["close"].ewm(span=20, adjust=False).mean()
    keltner_atr = ta.volatility.AverageTrueRange(d["high"], d["low"], d["close"], window=20).average_true_range()
    kc_upper = keltner_ma + (1.5 * keltner_atr)
    kc_lower = keltner_ma - (1.5 * keltner_atr)
    d["squeeze_aktif"] = ((d["bb_lower"] > kc_lower) & (d["bb_upper"] < kc_upper)).astype(float)
    d["squeeze_momentum"] = (d["close"] - d["bb_mid"]) / (d["atr"] + 1e-9)

    # ADX Trend Gücü
    adx_ind = ta.trend.ADXIndicator(d["high"], d["low"], d["close"], window=14)
    d["adx"] = adx_ind.adx()
    di_plus = adx_ind.adx_pos()
    di_minus = adx_ind.adx_neg()
    d["adx_trend_gucu"] = (d["adx"] / 100.0) * np.sign(di_plus - di_minus)

    # Supertrend Hesabı (Hızlı versiyon)
    supertrend = pd.Series(0.0, index=d.index)
    direction = pd.Series(1, index=d.index)
    for i in range(1, len(d)):
        if d["close"].iloc[i] > st_upper.iloc[i-1]: direction.iloc[i] = 1
        elif d["close"].iloc[i] < st_lower.iloc[i-1]: direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
            if direction.iloc[i] == 1 and st_lower.iloc[i] < st_lower.iloc[i-1]: st_lower.iloc[i] = st_lower.iloc[i-1]
            if direction.iloc[i] == -1 and st_upper.iloc[i] > st_upper.iloc[i-1]: st_upper.iloc[i] = st_upper.iloc[i-1]
    d["supertrend_sinyal"] = direction

    # ─── 8. GÜÇLENDİRİLMİŞ SMC (Volume Profile & Order Block) ───
    poc = volume_profile_poc(d)
    d["poc_mesafe"] = (d["close"] - poc) / (poc + 1e-9)
    
    ob_seviye, ob_tip = order_block_bul(d)
    d["ob_mesafe"] = (d["close"] - ob_seviye) / (ob_seviye + 1e-9) if ob_seviye > 0 else 0
    d["ob_tip"] = ob_tip

    # ─── 9. RSI UYUMSUZLUĞU (Divergence) ───
    # Bullish Divergence: Fiyat yeni dip yaparken RSI yapmıyorsa
    # Bearish Divergence: Fiyat yeni tepe yaparken RSI yapmıyorsa
    d["bull_div"] = 0
    d["bear_div"] = 0
    
    # Son 30 mumda ara
    for i in range(len(d)-5, len(d)-1):
        # Bullish
        if d["low"].iloc[i] < d["low"].iloc[i-1] and d["rsi"].iloc[i] > d["rsi"].iloc[i-1]:
            d.at[d.index[-1], "bull_div"] = 1
        # Bearish
        if d["high"].iloc[i] > d["high"].iloc[i-1] and d["rsi"].iloc[i] < d["rsi"].iloc[i-1]:
            d.at[d.index[-1], "bear_div"] = 1

    # ─── 10. BOLLINGER BAND SQUEEZE (Daralma) ───
    d["bb_width"] = (d["bb_upper"] - d["bb_lower"]) / (d["bb_mid"] + 1e-9)
    d["bb_squeeze"] = np.where(d["bb_width"] < d["bb_width"].rolling(100).quantile(0.2), 1, 0)
    
    d["ma20"] = d["ema20"]
    d["ma50"] = d["ema50"]

    d = d.fillna(0)
    return d


def _binance_mum_cek(sembol, interval="15m", limit=1000):
    """Binance API üzerinden OHLCV verilerini çeker."""
    url = f"https://api.binance.com/api/v3/klines?symbol={sembol}&interval={interval}&limit={limit}"
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        df = pd.DataFrame(r.json(), columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "_", "_", "_", "_", "_", "_"
        ])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("datetime", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["symbol"] = sembol
        return df[['symbol', 'open', 'high', 'low', 'close', 'volume']]
    return None

def kapsamli_veri_cek(sembol, interval="15m", n_bars=1000):
    data_main = None
    for attempt in range(3):
        try:
            # --- 1. İSTEK (Ana Veri) ---
            data_main = _binance_mum_cek(sembol, interval, limit=n_bars)
            if data_main is not None and not data_main.empty:
                break
            time.sleep(1)
        except:
            time.sleep(2)
            continue
    else:
        return None

    try:
        # Sadece 15m (varsayılan) çekiliyorsa MTF onayı ekle
        is_default = (interval == "15m")
        
        if is_default:
            data_1h = _binance_mum_cek(sembol, "1h", limit=200)
            data_4h = _binance_mum_cek(sembol, "4h", limit=100)
        else:
            data_1h, data_4h = None, None
            
    except Exception as e:
        print(f"\n⚠️ [{sembol}] Binance MTF Bağlantı Hatası: {e}")
        return None

    if len(data_main) < 100:
        print(f"❌ [{sembol}] Yetersiz veri uzunluğu: {len(data_main)}")
        return None

    # Ana indikatörleri hesapla
    data = indiktor_hesapla(data_main)

    # Üst Zaman Dilimi Analizi (Trend Onayı)
    # 1 Saatlik Onay
    if data_1h is not None and not data_1h.empty:
        h1 = indiktor_hesapla(data_1h).iloc[-1]
        data["trend_1h_yukari"] = int(h1["supertrend_sinyal"] == 1)
        data["adx_1h"] = h1["adx"]
    else:
        data["trend_1h_yukari"], data["adx_1h"] = 0, 20.0

    # 4 Saatlik Onay
    if data_4h is not None and not data_4h.empty:
        h4 = indiktor_hesapla(data_4h).iloc[-1]
        data["trend_4h_yukari"] = int(h4["supertrend_sinyal"] == 1)
        data["adx_4h"] = h4["adx"]
    else:
        data["trend_4h_yukari"], data["adx_4h"] = 0, 20.0

    # Dış Veriler
    try:
        from modules.intelligence import order_book_imbalance_hesapla
        data["korku_acgozluluk_endeksi"] = korku_acgozluluk_getir()
        data["emir_defteri_basinci"] = emir_defteri_analizi(sembol)
        
        m_risk, spx_kor = makro_risk_analizi()
        data["makro_risk_skoru"] = m_risk
        data["spx_korelasyon"] = spx_kor
        
        data["funding_rate"] = funding_rate_getir(sembol)
        data["balina_baskisi"] = balina_emirleri_cek(sembol)
        
        imb_data = order_book_imbalance_hesapla(sembol)
        data["order_book_imbalance"] = imb_data["imbalance"]
        data["spoofing_metric"] = imb_data["spoofing_metric"]
    except Exception as e:
        print(f"⚠️ [{sembol}] Yan veriler çekilirken hata oluştu: {e}")
        data["korku_acgozluluk_endeksi"] = 0.5
        data["emir_defteri_basinci"] = 1.0
        data["makro_risk_skoru"] = 0.5
        data["spx_korelasyon"] = 0.0
        data["funding_rate"] = 0.0
        data["balina_baskisi"] = 0.5
        data["order_book_imbalance"] = 0.5
        data["spoofing_metric"] = 0.0

    return data