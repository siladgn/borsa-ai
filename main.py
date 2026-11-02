import os
import time
import threading
import pandas as pd
import numpy as np
import requests
import warnings
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
from flask import Flask, request, jsonify

# Loglama yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/borsa_ai.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Telegram ve HTTP isteklerinin sürekli ekrana yazmasını (spam) gizle
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# Modüllerini içe aktar
from modules.teknik import kapsamli_veri_cek
from modules.ai_model import yapay_zeka_analizi
from modules.news import haber_duyarliligi
from modules import paper_trader, database, mtf_engine, liquidity, intelligence
from modules.trade_intent_executor import try_process_ui_trade_intent
from telegram_bot import telegram_mesaj, telegram_dinleyici_baslat, kaldirac_hesapla
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from modules.intelligence import risk_odul_hesapla

def generate_position_buttons(sembol):
    keyboard = [
        [
            InlineKeyboardButton("📊 Analiz", callback_data=f"analiz_{sembol}"),
            InlineKeyboardButton("❌ Kapat", callback_data=f"kapat_{sembol}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Proje klasörünü bul ve .env yükle (Kesin çözüm)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TOPLAM_BAKIYE, ISLEM_BASI_RISK,
    PAPER_TRADING, PAPER_START_BALANCE, CRYPTO_FLIP_ON_SIGNAL,
    PAPER_MAX_OPEN_POSITIONS, PAPER_SEND_DAILY_REPORT,
    PAPER_SEND_WEEKLY_REPORT, CRYPTO_FLIP_LONG_MIN_SCORE,
    CRYPTO_FLIP_SHORT_MAX_SCORE,
    AUTO_BINANCE_TRADING,
    MIN_DAILY_TRADES,
)

warnings.filterwarnings('ignore')

# --- 🚀 AYARLAR ---
takip_listesi = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", 
    "XRPUSDT", "ADAUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT"
]

def get_fear_greed_index():
    try:
        r = requests.get("https://api.alternative.me/fng/")
        data = r.json()
        return int(data['data'][0]['value']), data['data'][0]['value_classification']
    except:
        return 50, "Neutral"

def ana_dongu():
    print("🚀 BorsaAI PRO - KRİPTO MODU DEVREDE!")
    print("7/24 Kesintisiz Tarama Başlıyor...\n")
    
    # Başlangıç bildirimi
    database.log_ekle("Sistem başlatıldı. 7/24 Tarama başlıyor...", "INFO", "SISTEM")
    telegram_mesaj("🚀 <b>BorsaAI PRO Başlatıldı!</b>\n\nAnaliz ve işlem motoru aktif. Piyasalar taranıyor...")

    if PAPER_TRADING:
        paper_trader.init_balance_if_needed(PAPER_START_BALANCE)
        print(f"🧾 Paper Trading AÇIK. Bakiye: {paper_trader.get_balance(PAPER_START_BALANCE):.2f}")
        print(
            f"📌 Günlük min işlem kotası: {MIN_DAILY_TRADES} "
            "(dolu değilse gevşek giriş, küçük kârda TP1; 0=kapalı)\n"
        )
    else:
        print(
            "⚠️ PAPER KAPALI — GERÇEK BİNANCE VADELİ.\n"
            f"   Otomatik sinyal girişi: {'AÇIK' if AUTO_BINANCE_TRADING else 'KAPALI'} (AUTO_BINANCE_TRADING)\n"
            f"   Günlük min işlem kotası: {MIN_DAILY_TRADES} (0=kapalı; altında gevşek giriş, küçük kâr için TP1)\n"
        )

    while True:
        try:
            logger.info("Piyasa tarama döngüsü başladı...")
            database.log_ekle("Piyasa tarama döngüsü başladı...", "DEBUG", "DONGU")

            # Dashboard → data/ui_trade_intent.json (yalnızca PAPER_TRADING=False)
            try:
                ui_msg = try_process_ui_trade_intent(telegram_mesaj)
                if ui_msg:
                    database.log_ekle(ui_msg[:900], "INFO", "UI_INTENT")
            except Exception as ui_e:
                logger.error("UI trade intent: %s", ui_e)
                database.log_ekle(f"UI_INTENT hata: {ui_e}", "ERROR", "UI_INTENT")
            
            fng_val, fng_class = get_fear_greed_index()
            acik_sinyaller = database.acik_islemleri_yukle()
            
            # --- DEVRE KESİCİ (Circuit Breaker) ---
            devre_kesici = paper_trader.check_circuit_breaker(max_loss_percent=0.03) # Günlük %3 kayıpta dur
            if devre_kesici:
                print("🛑 DEVRE KESİCİ AKTİF! Günlük kayıp limiti aşıldı. Bugünlük işlem durduruldu.")
                database.log_ekle("Devre kesici aktif. Günlük kayıp limiti aşıldı.", "WARNING", "SISTEM")
                time.sleep(3600) # 1 saat bekle
                continue

            if PAPER_TRADING:
                paper_trader.maybe_send_reports(
                    telegram_mesaj,
                    daily=PAPER_SEND_DAILY_REPORT,
                    weekly=PAPER_SEND_WEEKLY_REPORT,
                )
            
            # --- 0. MULTI-SYMBOL ZEKASI (BTC/ETH GÜÇ KONTROLÜ) ---
            print("🔗 Korelasyon ve Göreceli Güç Analizi yapılıyor...")
            btc_data = kapsamli_veri_cek("BTCUSDT")
            eth_data = kapsamli_veri_cek("ETHUSDT")
            
            market_ok = True
            if btc_data is not None and eth_data is not None:
                btc_rsi = btc_data.iloc[-1]['rsi']
                eth_rsi = eth_data.iloc[-1]['rsi']
                # Eğer BTC ve ETH RSI değerleri çok farklıysa (ayrışma varsa) piyasa kararsızdır
                if abs(btc_rsi - eth_rsi) > 25:
                    print("⚠️ BTC ve ETH arasında aşırı ayrışma var, yeni işlem riskli!")
                    market_ok = False
        
            for sembol in takip_listesi:
                try:
                    trades_today_ct = database.bugun_acilan_islem_sayisi()
                    kota_alti = (
                        MIN_DAILY_TRADES > 0 and trades_today_ct < MIN_DAILY_TRADES
                    )
                    if sembol == takip_listesi[0] and kota_alti:
                        print(
                            f"📌 Günlük işlem {trades_today_ct}/{MIN_DAILY_TRADES} — kota altı: "
                            "gevşek giriş; kâr küçük olsa bile alınır (hedef TP1)."
                        )

                    data = btc_data if sembol == "BTCUSDT" else (eth_data if sembol == "ETHUSDT" else kapsamli_veri_cek(sembol))
                    if data is None or data.empty:
                        continue

                    # Analiz ve Karar Mekanizması
                    karar, guven_skoru, yon = yapay_zeka_analizi(data, sembol)
                    database.tahmin_kaydet(sembol, yon, guven_skoru) # Tahmini DB'ye kaydet
                    
                    # HABER DUYARLILIĞI ANALİZİ
                    # Not: haber_duyarliligi fonksiyonunun -1 (negatif) ile 1 (pozitif) arası bir skor döndürdüğünü varsayıyoruz.
                    haber_skoru = haber_duyarliligi(sembol)
                    logger.info(f"[{sembol}] Haber Duyarlılık Skoru: {haber_skoru:.2f}")
                    
                    # --- HABERLERİN GÜVEN SKORUNA DİNAMİK ETKİSİ ---
                    # Pozitif haber LONG güvenini, negatif haber SHORT güvenini artırır.
                    if yon == "LONG":
                        guven_skoru += (haber_skoru * 10) 
                    elif yon == "SHORT":
                        guven_skoru -= (haber_skoru * 10)
                    guven_skoru = max(0, min(100, guven_skoru))

                    anlik_fiyat = data.iloc[-1]['close']
                    atr = data.iloc[-1]['atr']
                    adx = data.iloc[-1].get('adx', 20)

                    # --- 1. MTF ANALİZİ (ULTRA ZEKA) ---
                    mtf_skor, mtf_detay = mtf_engine.mtf_skor_hesapla(sembol)
                    
                    # --- 2. LİKİDİTE VE VOLATİLİTE ANALİZİ ---
                    likidite = liquidity.likidite_bolgeleri_bul(data)
                    vol_tahmin = liquidity.volatilite_tahmini(data)
                    
                    # Sembol bazlı onay durumu (global market_ok'u diğer sembollere bulaştırma)
                    symbol_market_ok = market_ok

                    # --- 2.5 BALİNA DUVARLARI ANALİZİ ---
                    balina_verisi = intelligence.balina_duvarlari_bul(sembol)
                    if balina_verisi:
                        print(f"🐋 [{sembol}] Balina Baskısı: {balina_verisi['duvar_gucu']:.2f}")
                        if yon == "LONG" and balina_verisi['duvar_gucu'] < 0.5:
                            print(f"⚠️ [{sembol}] LONG elendi: Satış baskısı (Balina duvarları) çok güçlü.")
                            symbol_market_ok = False
                        elif yon == "SHORT" and balina_verisi['duvar_gucu'] > 1.5:
                            print(f"⚠️ [{sembol}] SHORT elendi: Alış baskısı (Balina duvarları) çok güçlü.")
                            symbol_market_ok = False
                    
                    # Trend ve Volatilite Kontrolü
                    ana_trend_yukari = data.iloc[-1]['trend_4h_yukari'] == 1
                    supertrend_15m = data.iloc[-1]['supertrend_sinyal']
                    funding = data.iloc[-1].get('funding_rate', 0.0)
                    
                    # Divergence Kontrolü
                    bull_div = data.iloc[-1].get('bull_div', 0)
                    bear_div = data.iloc[-1].get('bear_div', 0)
                    close_prev = data.iloc[-2]['close'] if len(data) > 2 else anlik_fiyat
                    ema20_prev = data.iloc[-2].get('ema20', anlik_fiyat) if len(data) > 2 else anlik_fiyat

                    # --- 2.1 PİYASA REJİMİ TESPİTİ (Trend vs Range) ---
                    piyasa_trendde = adx > 20 # 25'ten 20'ye çekildi
                    choppiness = 100 * np.log10( (data['atr'].rolling(14).sum()) / (data['high'].rolling(14).max() - data['low'].rolling(14).min() + 1e-9) ) / np.log10(14)
                    is_choppy = choppiness.iloc[-1] > 75.0 # 70'den 75'e esnetildi

                    # --- 3. DİNAMİK RİSK VE TP/SL YÖNETİMİ ---
                    # 1. Kelly Criterion ile Dinamik Kasa Riski Hesabı
                    # win_rate: 0.55 (varsayılan), risk_reward: 2.0 (tp2 / sl_mesafesi)
                    kelly_risk_orani = intelligence.kelly_risk_hesapla(guven_skoru)
                    
                    # 2. Dinamik Risk Çarpanı (Duyarlılık ve Güven)
                    dinamik_risk_carpani = kelly_risk_orani / ISLEM_BASI_RISK # Kelly / %2
                    
                    if guven_skoru > 75: dinamik_risk_carpani += 0.2 # Ekstra çarpan
                    elif guven_skoru < 55: dinamik_risk_carpani -= 0.1 

                    # --- 2.2 KORELASYON KONTROLÜ (Risk Yönetimi) ---
                    # Eğer diğer ana coin zaten aynı yönde bir işleme sahipse, risk iştahını azalt
                    for acik_sembol, acik_detay in acik_sinyaller.items():
                        if acik_sembol != sembol:
                            if acik_detay.get('yön') == yon:
                                print(f"⚠️ [{sembol}] Korelasyon Riski: {acik_sembol} zaten {yon} yönünde! Risk hafif düşürüldü.")
                                dinamik_risk_carpani *= 0.8 # %40'tan %20 düşüşe çekildi

                    # --- 2.3 SEANS KONTROLÜ (Time Filtering) ---
                    # Hafta sonu düşük likidite veya gece yarısı volatilite kontrolü
                    now = datetime.now()
                    is_weekend = now.weekday() >= 5 # Cumartesi(5), Pazar(6)
                    seans_ok = True
                    if is_weekend:
                        print(f"☕ [{sembol}] Hafta sonu: Daha agresif mod devrede.")
                        guven_skoru -= 2 # 5'ten 2'ye çekildi
                        if vol_tahmin['gerginlik'] < 0.2: seans_ok = False # 0.3'ten 0.2'ye çekildi

                    # --- 2.4 EKONOMİK TAKVİM KORUMASI (FOMC/CPI) ---
                    # Önemli ekonomik veriler açıklandığında volatilite kontrolü
                    takvim_ok = True
                    # Örnek: Önemli haber saatleri (UTC olarak girilmeli)
                    kritik_saatler = [
                        "2024-03-20 18:00", # FOMC Örneği
                        "2024-04-10 12:30", # CPI Örneği
                    ]
                    for ks in kritik_saatler:
                        ks_dt = datetime.strptime(ks, "%Y-%m-%d %H:%M")
                        if abs((now - ks_dt).total_seconds()) < 1800: # 1 saatten 30 dakikaya çekildi
                            takvim_ok = False
                            print(f"⚠️ [{sembol}] KRİTİK EKONOMİK VERİ SAATİ! İşlem engellendi.")
                    
                    # --- 3. DİNAMİK RİSK VE TP/SL YÖNETİMİ (SCALPING MODU) ---
                    # Güven skoru ve volatiliteye göre ATR çarpanlarını ayarla
                    # Win Rate odaklı ayarlar: TP1 yakın, SL uzak
                    atr_carpani_sl = 1.5
                    atr_carpani_tp1 = 0.7
                    atr_carpani_tp2 = 1.5

                    if guven_skoru > 75:
                        # Çok dar stoplar iyi setup'larda bile erken stop yaptırıyordu.
                        atr_carpani_sl = 1.5
                        atr_carpani_tp2 = 2.0  # Biraz daha geniş hedef
                    elif guven_skoru < 55:
                        atr_carpani_sl = 2.0   # Düşük güvende gürültüye karşı daha güvenli stop
                        atr_carpani_tp2 = 1.2  # Daha dar hedef (ulaşılabilir)
                    
                    # Volatilite/Korku Ayarı: FNG düşükse stopu biraz daha genişlet (Gürültüden kaçın)
                    if fng_val < 20:
                        atr_carpani_sl *= 1.3

                    # Likidite listesinden en yakın destek ve direnci seç
                    destek = anlik_fiyat * 0.95
                    direnc = anlik_fiyat * 1.05
                    if likidite:
                        alt_bolgeler = [b['fiyat'] for b in likidite if b['fiyat'] < anlik_fiyat]
                        ust_bolgeler = [b['fiyat'] for b in likidite if b['fiyat'] > anlik_fiyat]
                        if alt_bolgeler: destek = max(alt_bolgeler)
                        if ust_bolgeler: direnc = min(ust_bolgeler)

                    # LONG için Seviyeler
                    stop_loss = max(anlik_fiyat - (atr * atr_carpani_sl), destek * 0.995)
                    tp1 = anlik_fiyat + (atr * atr_carpani_tp1)
                    tp2 = min(anlik_fiyat + (atr * atr_carpani_tp2), direnc * 1.005)

                    # SHORT için Seviyeler
                    stop_short = min(anlik_fiyat + (atr * atr_carpani_sl), direnc * 1.005)
                    tp1_short = anlik_fiyat - (atr * atr_carpani_tp1)
                    tp2_short = max(anlik_fiyat - (atr * atr_carpani_tp2), destek * 0.995)


                    
                    # Fear & Greed Filtresi (Aşırı korkuda LONG, aşırı açgözlülükte SHORT riskli)
                    fng_ok = True
                    if yon == "LONG" and fng_val > 90: fng_ok = False # 80'den 90'a çekildi
                    if yon == "SHORT" and fng_val < 10: fng_ok = False # 20'den 10'a çekildi

                    # Funding Rate Filtresi (Kalabalığa karşı dikkat)
                    funding_ok = True
                    if yon == "LONG" and funding > 0.1: funding_ok = False # 0.05'ten 0.1'e çekildi
                    if yon == "SHORT" and funding < -0.1: funding_ok = False # -0.05'ten -0.1'e çekildi

                    kasa = paper_trader.get_balance(TOPLAM_BAKIYE) if PAPER_TRADING else TOPLAM_BAKIYE
                    riske_atilan_tutar = kasa * (ISLEM_BASI_RISK * dinamik_risk_carpani)
                    # Alınacak adet artık LONG / SHORT özelinde kaldıraçla birlikte orada hesaplanacak

                    # --- TELEGRAM VE DB DURUM GÜNCELLEME ---
                    if sembol in acik_sinyaller:
                        mevcut_islem = acik_sinyaller[sembol]
                        mevcut_islem['anlik_fiyat'] = anlik_fiyat
                        database.islem_kaydet(sembol, mevcut_islem)

                    # --- STRATEJİ KARARLARI (LONG/SHORT/EXIT) ---
                    now_iso = pd.Timestamp.now().isoformat()
                    
                    # Paper Trader Güncelleme (TP/SL kontrolü)
                    if PAPER_TRADING:
                        event_msg, event_data = paper_trader.update_position(
                            sembol, float(anlik_fiyat), now_iso, 
                            high=float(data.iloc[-1]['high']), 
                            low=float(data.iloc[-1]['low']),
                            atr=float(atr)
                        )
                        if event_msg: 
                            telegram_mesaj(event_msg)
                            # Kapanış varsa veritabanını da güncelle (Taktiksel İstihbarat için)
                            if event_data and event_data.get("type") == "CLOSE":
                                database.islem_kapat(sembol, anlik_fiyat, event_data.get("pnl", 0))

                    # BB Squeeze Kontrolü (Patlama Beklentisi)
                    is_squeezed = data.iloc[-1].get('bb_squeeze', 0) == 1
                    if is_squeezed:
                        print(f"🧨 [{sembol}] Bollinger Band Daralması: Sert hareket bekleniyor!")

                    # --- 2.3 VOLUME SPREAD ANALYSIS (VSA) ---
                    vol_avg = data['volume'].rolling(20).mean().iloc[-1]
                    vol_now = data['volume'].iloc[-1]
                    fiyat_degisim = abs(data['close'].iloc[-1] - data['open'].iloc[-1])
                    fiyat_degisim_avg = (data['high'] - data['low']).rolling(20).mean().iloc[-1]
                    
                    vsa_ok = True
                    if vol_now > vol_avg * 2.5 and fiyat_degisim < fiyat_degisim_avg * 0.3:
                        vsa_ok = False
                        print(f"⚠️ [{sembol}] VSA Uyarısı: Aşırı yüksek hacim, düşük sonuç! (Yorulma)")
                    if vol_now < vol_avg * 0.5:
                        vsa_ok = False
                        print(f"⚠️ [{sembol}] VSA Uyarısı: Düşük hacimle hareket (Sahte çıkış/düşüş olabilir)")

                    # --- 2.6 MOMENTUM KALİTESİ (Yanlış "POTANSİYEL AL" filtreleme) ---
                    rsi_now = data.iloc[-1].get('rsi', 50)
                    ema20 = data.iloc[-1].get('ema20', anlik_fiyat)
                    ema50 = data.iloc[-1].get('ema50', anlik_fiyat)
                    volume_ratio = data.iloc[-1].get('volume_ratio', 1.0)
                    long_setup_ok = (
                        (anlik_fiyat > ema20 >= ema50) and
                        (close_prev > ema20_prev) and
                        (supertrend_15m == 1) and
                        (adx >= 22) and
                        (rsi_now >= 54) and
                        (volume_ratio >= 1.1) and
                        (data.iloc[-1]['trend_1h_yukari'] == 1) and
                        ana_trend_yukari
                    )
                    short_setup_ok = (
                        (anlik_fiyat < ema20 <= ema50) and
                        (close_prev < ema20_prev) and
                        (supertrend_15m == -1) and
                        (adx >= 22) and
                        (rsi_now <= 46) and
                        (volume_ratio >= 1.1) and
                        (data.iloc[-1]['trend_1h_yukari'] == 0) and
                        (not ana_trend_yukari)
                    )

                    # --- 2.4 FNG & TREND UYUMU ---
                    fng_trend_ok = True
                    if yon == "LONG" and fng_val < 20:
                        if not bull_div:
                            fng_trend_ok = False
                            print(f"⚠️ [{sembol}] FNG Uyarısı: Aşırı korkuda LONG için dönüş sinyali (Bull Div) yok.")
                    
                    # --- 2.5 HABER FİLTRESİ ---
                    haber_ok = True
                    if yon == "LONG" and haber_skoru < -0.4:
                        haber_ok = False
                        logger.warning(f"[{sembol}] LONG engellendi: Haberler negatif ({haber_skoru:.2f})")
                    elif yon == "SHORT" and haber_skoru > 0.4:
                        haber_ok = False
                        logger.warning(f"[{sembol}] SHORT engellendi: Haberler pozitif ({haber_skoru:.2f})")

                    # Günlük kota altında: daha çok işlem; kâr potansiyeli küçük olsa TP1 ile realize
                    guven_min = 52 if kota_alti else 62
                    mtf_long_min = 6 if kota_alti else 12
                    mtf_short_max = -6 if kota_alti else -12
                    rr_min = 1.05 if kota_alti else 1.5
                    long_setup_scalp = (
                        (anlik_fiyat > ema20)
                        and (supertrend_15m == 1)
                        and (adx >= 16)
                        and (rsi_now >= 48)
                        and (volume_ratio >= 0.95)
                    )
                    short_setup_scalp = (
                        (anlik_fiyat < ema20)
                        and (supertrend_15m == -1)
                        and (adx >= 16)
                        and (rsi_now <= 52)
                        and (volume_ratio >= 0.95)
                    )
                    long_entry_setup = long_setup_ok or (kota_alti and long_setup_scalp)
                    short_entry_setup = short_setup_ok or (kota_alti and short_setup_scalp)
                    vsa_eff = vsa_ok or kota_alti
                    choppy_eff = (not is_choppy) or kota_alti
                    fng_trend_eff = fng_trend_ok or kota_alti
                    seans_eff = seans_ok or kota_alti
                    tp_take_long = tp1 if kota_alti else tp2
                    tp_take_short = tp1_short if kota_alti else tp2_short

                    # --- RİSK/ÖDÜL FİLTRESİ (Komisyon dahil) ---
                    rr_ok = True
                    if yon == "LONG":
                        rr_ratio = risk_odul_hesapla(anlik_fiyat, stop_loss, tp_take_long)
                    elif yon == "SHORT":
                        rr_ratio = risk_odul_hesapla(anlik_fiyat, stop_short, tp_take_short)
                    else:
                        rr_ratio = 0.0
                    if rr_ratio < rr_min:
                        rr_ok = False
                        if yon != "EXIT":
                            print(f"⚠️ [{sembol}] R:R Yetersiz ({rr_ratio:.2f} < {rr_min})")

                    # --- LONG Giriş Şartı ---
                    is_long = (
                        yon == "LONG"
                        and guven_skoru >= guven_min
                        and mtf_skor >= mtf_long_min
                        and symbol_market_ok
                        and fng_ok
                        and fng_trend_eff
                        and funding_ok
                        and haber_ok
                        and rr_ok
                        and choppy_eff
                        and seans_eff
                        and takvim_ok
                        and vsa_eff
                        and long_entry_setup
                    )

                    # --- SHORT Giriş Şartı ---
                    is_short = (
                        yon == "SHORT"
                        and guven_skoru >= guven_min
                        and mtf_skor <= mtf_short_max
                        and symbol_market_ok
                        and fng_ok
                        and fng_trend_eff
                        and funding_ok
                        and haber_ok
                        and rr_ok
                        and choppy_eff
                        and seans_eff
                        and takvim_ok
                        and vsa_eff
                        and short_entry_setup
                    )

                    # --- ENGEL SEBEPLERİNİ HESAPLA ---
                    reasons = []
                    if yon == "EXIT":
                        reasons.append("Yön EXIT")
                    if yon == "LONG" and mtf_skor < mtf_long_min:
                        reasons.append(f"Zayıf MTF ({mtf_skor:.1f} < {mtf_long_min})")
                    if yon == "SHORT" and mtf_skor > mtf_short_max:
                        reasons.append(f"Zayıf MTF ({mtf_skor:.1f} > {mtf_short_max})")
                    if not symbol_market_ok:
                        reasons.append("Market Uyumsuz")
                    if not fng_ok:
                        reasons.append(f"F&G Riskli ({fng_val})")
                    if not fng_trend_eff:
                        reasons.append("FNG/Trend uyumsuz")
                    if not haber_ok:
                        reasons.append(f"Haberler Ters Yönde ({haber_skoru:.2f})")
                    if not funding_ok:
                        reasons.append(f"Funding Riskli ({funding})")
                    if not rr_ok:
                        reasons.append(f"R:R Yetersiz ({rr_ratio:.2f} < {rr_min})")
                    if not choppy_eff:
                        reasons.append("Testere Piyasası (Choppy)")
                    if not seans_eff:
                        reasons.append("Düşük Volatilite (Seans)")
                    if not takvim_ok:
                        reasons.append("Ekonomik Takvim Engeli")
                    if guven_skoru < guven_min:
                        reasons.append(f"Düşük Güven (%{guven_skoru:.1f} < {guven_min})")
                    if not vsa_eff:
                        reasons.append("VSA Veto (Hacim/Fiyat Uyumsuz)")
                    if yon == "LONG" and not long_entry_setup:
                        reasons.append("Long setup zayıf (kota altında scalp alternatifi)")
                    if yon == "SHORT" and not short_entry_setup:
                        reasons.append("Short setup zayıf (kota altında scalp alternatifi)")

                    # --- GÖRSEL BİLGİLENDİRME ---
                    if sembol not in acik_sinyaller:
                        print(f"\n🔍 [{sembol}] ANALİZ RAPORU:")
                        print(f"   Karar: {karar} | Yön: {'📈 LONG' if yon == 'LONG' else '📉 SHORT' if yon == 'SHORT' else '⏳ BEKLE'}")
                        print(f"   AI Güven: %{guven_skoru:.1f} | MTF Skor: {mtf_skor:.1f} | R:R: {rr_ratio:.2f}")
                        if yon == "LONG":
                            print(
                                f"   Hedef TP: {tp_take_long:.2f} ({'TP1 (kota: küçük kâr yeter)' if kota_alti else 'TP2'}) | SL: {stop_loss:.2f}"
                            )
                        elif yon == "SHORT":
                            print(
                                f"   Hedef TP: {tp_take_short:.2f} ({'TP1 (kota: küçük kâr yeter)' if kota_alti else 'TP2'}) | SL: {stop_short:.2f}"
                            )
                        print(f"   Durum: {'✅ ŞARTLAR UYGUN' if (is_long or is_short) else '❌ ŞARTLAR OLUŞMADI'}")
                        if not (is_long or is_short):
                            print(f"   Engel: {', '.join(reasons)}")

                    if is_long and sembol not in acik_sinyaller:
                        acik_adet = (
                            paper_trader.count_open_positions()
                            if PAPER_TRADING
                            else len(database.acik_islemleri_yukle())
                        )
                        if acik_adet < PAPER_MAX_OPEN_POSITIONS:
                            kaldirac_str, secilen_kaldirac = kaldirac_hesapla(guven_skoru, atr, anlik_fiyat)
                            pozisyon_dolar = riske_atilan_tutar * secilen_kaldirac
                            alinacak_adet = pozisyon_dolar / anlik_fiyat
                            neden_ozet = f"AI:%{guven_skoru:.0f}, MTF:{mtf_skor:.0f}, FNG:{fng_val}"
                            if balina_verisi: neden_ozet += f", Whale:{balina_verisi['duvar_gucu']:.1f}"
                            if kota_alti: neden_ozet += "|GUNLUK_KOTA_TP1"

                            if PAPER_TRADING:
                                paper_trader.open_position(
                                    sembol, "LONG", anlik_fiyat, alinacak_adet, stop_loss, tp1, tp_take_long, now_iso
                                )
                                msg = (f"🟢 <b>PAPER LONG AÇILDI</b> — {sembol}\n"
                                       f"Fiyat: <code>{anlik_fiyat:.2f}</code> | SL: <code>{stop_loss:.2f}</code>\n"
                                       f"TP1: <code>{tp1:.2f}</code> | Ana hedef: <code>{tp_take_long:.2f}</code>\n"
                                       f"Güven: <code>%{guven_skoru:.1f}</code> | Kaldıraç: <code>{kaldirac_str}</code>")
                                telegram_mesaj(msg, reply_markup=generate_position_buttons(sembol))
                                database.islem_kaydet(sembol, {
                                    "side": "LONG", "giris_fiyat": anlik_fiyat, "miktar": alinacak_adet,
                                    "stop_loss": stop_loss, "take_profit": tp_take_long, "ai_guven": guven_skoru,
                                    "neden": neden_ozet
                                })
                            else:
                                if not AUTO_BINANCE_TRADING:
                                    logger.info(
                                        "[%s] LONG sinyali — AUTO_BINANCE_TRADING kapalı, borsaya emir yok",
                                        sembol,
                                    )
                                else:
                                    try:
                                        from modules.binance_trader import gercek_islem_ac
                                        lev_i = max(1, min(125, int(round(float(secilen_kaldirac)))))
                                        giris, miktar, sl_tp_uyari = gercek_islem_ac(
                                            sembol, "LONG", alinacak_adet, lev_i, stop_loss, tp_take_long
                                        )
                                        database.islem_kaydet(sembol, {
                                            "side": "LONG",
                                            "giris_fiyat": giris,
                                            "miktar": miktar,
                                            "stop_loss": stop_loss,
                                            "take_profit": tp_take_long,
                                            "ai_guven": guven_skoru,
                                            "neden": neden_ozet + "|AUTO_BINANCE",
                                        })
                                        msg = (f"🟢 <b>GERÇEK LONG (OTOMATİK)</b> — {sembol}\n"
                                               f"Giriş: <code>{giris:.6f}</code> | SL: <code>{stop_loss:.4f}</code>\n"
                                               f"TP: <code>{tp_take_long:.4f}</code> | Güven: <code>%{guven_skoru:.1f}</code> | Kaldıraç: <code>{lev_i}x</code> İzole")
                                        if sl_tp_uyari:
                                            msg += f"\n⚠️ SL/TP: <code>{sl_tp_uyari}</code>"
                                        telegram_mesaj(msg, reply_markup=generate_position_buttons(sembol))
                                    except Exception as be:
                                        logger.error("[%s] Gerçek LONG açılamadı: %s", sembol, be)
                                        database.log_ekle(f"[{sembol}] Gerçek LONG hata: {be}", "ERROR", sembol)
                                        try:
                                            telegram_mesaj(f"❌ <b>Otomatik LONG başarısız</b> — <code>{sembol}</code>\n<code>{be}</code>")
                                        except Exception:
                                            pass

                    elif is_short and sembol not in acik_sinyaller:
                        acik_adet = (
                            paper_trader.count_open_positions()
                            if PAPER_TRADING
                            else len(database.acik_islemleri_yukle())
                        )
                        if acik_adet < PAPER_MAX_OPEN_POSITIONS:
                            kaldirac_str, secilen_kaldirac = kaldirac_hesapla(guven_skoru, atr, anlik_fiyat)
                            pozisyon_dolar = riske_atilan_tutar * secilen_kaldirac
                            alinacak_adet = pozisyon_dolar / anlik_fiyat
                            neden_ozet = f"AI:%{guven_skoru:.0f}, MTF:{mtf_skor:.0f}, FNG:{fng_val}"
                            if balina_verisi: neden_ozet += f", Whale:{balina_verisi['duvar_gucu']:.1f}"
                            if kota_alti: neden_ozet += "|GUNLUK_KOTA_TP1"

                            if PAPER_TRADING:
                                paper_trader.open_position(
                                    sembol, "SHORT", anlik_fiyat, alinacak_adet, stop_short, tp1_short, tp_take_short, now_iso
                                )
                                msg = (f"🔴 <b>PAPER SHORT AÇILDI</b> — {sembol}\n"
                                       f"Fiyat: <code>{anlik_fiyat:.2f}</code> | SL: <code>{stop_short:.2f}</code>\n"
                                       f"TP1: <code>{tp1_short:.2f}</code> | Ana hedef: <code>{tp_take_short:.2f}</code>\n"
                                       f"Güven: <code>%{guven_skoru:.1f}</code> | Kaldıraç: <code>{kaldirac_str}</code>")
                                telegram_mesaj(msg, reply_markup=generate_position_buttons(sembol))
                                database.islem_kaydet(sembol, {
                                    "side": "SHORT", "giris_fiyat": anlik_fiyat, "miktar": alinacak_adet,
                                    "stop_loss": stop_short, "take_profit": tp_take_short, "ai_guven": guven_skoru,
                                    "neden": neden_ozet
                                })
                            else:
                                if not AUTO_BINANCE_TRADING:
                                    logger.info(
                                        "[%s] SHORT sinyali — AUTO_BINANCE_TRADING kapalı, borsaya emir yok",
                                        sembol,
                                    )
                                else:
                                    try:
                                        from modules.binance_trader import gercek_islem_ac
                                        lev_i = max(1, min(125, int(round(float(secilen_kaldirac)))))
                                        giris, miktar, sl_tp_uyari = gercek_islem_ac(
                                            sembol, "SHORT", alinacak_adet, lev_i, stop_short, tp_take_short
                                        )
                                        database.islem_kaydet(sembol, {
                                            "side": "SHORT",
                                            "giris_fiyat": giris,
                                            "miktar": miktar,
                                            "stop_loss": stop_short,
                                            "take_profit": tp_take_short,
                                            "ai_guven": guven_skoru,
                                            "neden": neden_ozet + "|AUTO_BINANCE",
                                        })
                                        msg = (f"🔴 <b>GERÇEK SHORT (OTOMATİK)</b> — {sembol}\n"
                                               f"Giriş: <code>{giris:.6f}</code> | SL: <code>{stop_short:.4f}</code>\n"
                                               f"TP: <code>{tp_take_short:.4f}</code> | Güven: <code>%{guven_skoru:.1f}</code> | Kaldıraç: <code>{lev_i}x</code> İzole")
                                        if sl_tp_uyari:
                                            msg += f"\n⚠️ SL/TP: <code>{sl_tp_uyari}</code>"
                                        telegram_mesaj(msg, reply_markup=generate_position_buttons(sembol))
                                    except Exception as be:
                                        logger.error("[%s] Gerçek SHORT açılamadı: %s", sembol, be)
                                        database.log_ekle(f"[{sembol}] Gerçek SHORT hata: {be}", "ERROR", sembol)
                                        try:
                                            telegram_mesaj(f"❌ <b>Otomatik SHORT başarısız</b> — <code>{sembol}</code>\n<code>{be}</code>")
                                        except Exception:
                                            pass
                    time.sleep(3) # TradingView koruması

                except Exception as e:
                    logger.error(f"[{sembol}] HATA: {e}")
                    database.log_ekle(f"[{sembol}] HATA: {e}", "ERROR", sembol)

        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            database.log_ekle(f"Ana döngü hatası: {e}", "ERROR", "DONGU")
            time.sleep(60)  # Hata durumunda 1 dakika bekle
        
        print("\n--- Tarama bitti. 3 dakika dinleniliyor... ---\n")
        time.sleep(180)

import psutil # Yeni kütüphane

def check_single_instance():
    """Aynı anda birden fazla botun çalışmasını engeller (PID Lock)."""
    pid_file = "data/bot.pid"
    if not os.path.exists("data"): os.makedirs("data")
    
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            
            if psutil.pid_exists(old_pid) and old_pid != os.getpid():
                proc = psutil.Process(old_pid)
                # Sadece python süreçlerini durdur (başka bir programın PID'sini kazara kapatmamak için)
                if "python" in proc.name().lower():
                    print(f"⚠️ [SYSTEM] Zaten çalışan bir bot örneği bulundu (PID: {old_pid}).")
                    print("🛑 Eski süreç sonlandırılıyor...")
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except:
                        proc.kill()
                    time.sleep(2)
        except Exception as e:
            print(f"⚠️ PID kontrol hatası: {e}")

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

# --- WEBHOOK (TELSİZ) SİSTEMİ ---
app = Flask(__name__)

@app.route('/tv-webhook', methods=['POST'])
def tradingview_webhook():
    data = request.json
    sembol = data.get('symbol')
    tv_yon = data.get('action')
    
    # Telsizden mesaj geldiğinde ekrana kocaman yazdıracak:
    print(f"\n🚨 [DİKKAT] TRADINGVIEW'DEN SİNYAL GELDİ: {sembol} - {tv_yon} 🚨\n")
    
    return jsonify({"status": "success"}), 200

def flask_baslat():
    app.run(host='0.0.0.0', port=5000)

if __name__ == "__main__":
    check_single_instance()
    
    # 1. Telsizi (Webhook) dinlemeye başla
    t_web = threading.Thread(target=flask_baslat, daemon=True)
    t_web.start()
    
    # 2. Telegram dinleyicisini başlat
    t = threading.Thread(target=telegram_dinleyici_baslat, daemon=True)
    t.start()

    # 3. Ana Döngüyü Başlat
    try:
        ana_dongu()
    except KeyboardInterrupt:
        print("Bot durduruldu.")