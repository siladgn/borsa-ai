import requests
import warnings
import json
import os
import asyncio
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, PAPER_TRADING
from datetime import datetime

# Yeni DB Modülü
from modules.database import islem_kaydet, acik_islemleri_yukle, islem_kapat, log_ekle

# --- AYARLAR VE DOSYA YOLU ---
# Çalışma klasörü değişse bile projedeki `data/` klasörünü kullan
BASE_DIR = os.path.abspath(os.path.dirname(__file__))  # borsaai/
DATA_DIR = os.path.join(BASE_DIR, "data")
ISLEMLER_DOSYASI = os.path.join(DATA_DIR, "acik_islemler.json")

# Veri klasörü yoksa oluştur
os.makedirs(DATA_DIR, exist_ok=True)

# --- 1. MESAJ GÖNDERME ---
def telegram_mesaj(metin, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": metin,
        "parse_mode": "HTML"
    }
    if reply_markup:
        # Not: requests ile gönderirken reply_markup'ı json string yapmalıyız
        payload["reply_markup"] = json.dumps(reply_markup.to_dict())
        
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            error_msg = f"[Telegram] Hata: {r.status_code} — {r.text}"
            print(error_msg)
            log_ekle(error_msg, "ERROR", "TELEGRAM")
    except Exception as e:
        error_msg = f"[Telegram] Bağlantı hatası: {e}"
        print(error_msg)
        log_ekle(error_msg, "ERROR", "TELEGRAM")

# --- 2. VERİ YÖNETİMİ (KAYIT VE YÜKLEME) ---
# --- 2. VERİ YÖNETİMİ (DB ÜZERİNDEN) ---
def islemleri_yukle():
    return acik_islemleri_yukle()

def islemi_kaydet_db(sembol, veri):
    islem_kaydet(sembol, veri)

def islem_sil_db(sembol, cikis_fiyat=0, kar_zarar=0):
    islem_kapat(sembol, cikis_fiyat, kar_zarar)


# ============================================================
#  AKILLI SOHBET — Doğal Dil Anlama
# ============================================================

# Hangi coin sorulduğunu bul
def _sembol_bul(metin):
    """Mesajdaki coin ismini BINANCE sembolüne çevir."""
    metin_lower = metin.lower()
    coin_map = {
        "btc": "BTCUSDT", "bitcoin": "BTCUSDT",
        "eth": "ETHUSDT", "ethereum": "ETHUSDT",
        "bnb": "BNBUSDT",
        "sol": "SOLUSDT", "solana": "SOLUSDT",
        "xrp": "XRPUSDT", "ripple": "XRPUSDT",
        "avax": "AVAXUSDT", "avalanche": "AVAXUSDT",
        "link": "LINKUSDT", "chainlink": "LINKUSDT",
        "doge": "DOGEUSDT", "dogecoin": "DOGEUSDT",
        "ada": "ADAUSDT", "cardano": "ADAUSDT",
        "dot": "DOTUSDT", "polkadot": "DOTUSDT",
        "fet": "FETUSDT",
        "inj": "INJUSDT", "injective": "INJUSDT",
        "rndr": "RNDRUSDT", "render": "RNDRUSDT",
        "near": "NEARUSDT",
        "sui": "SUIUSDT",
        "apt": "APTUSDT", "aptos": "APTUSDT",
        "arb": "ARBUSDT", "arbitrum": "ARBUSDT",
        "op": "OPUSDT", "optimism": "OPUSDT",
        "atom": "ATOMUSDT", "cosmos": "ATOMUSDT",
        "trx": "TRXUSDT", "tron": "TRXUSDT",
    }
    for anahtar, sembol in coin_map.items():
        if anahtar in metin_lower:
            return sembol
    return None

# Mesajın ne sorduğunu anla
def _niyet_bul(metin):
    """Kullanıcının ne istediğini basit anahtar kelimelerle tespit et."""
    m = metin.lower()
    
    # Kasa / Bakiye soruları
    kasa_keys = ["kasa", "bakiye", "para", "sermaye", "ne kadar var", "hesap", "dolar", "balance"]
    if any(k in m for k in kasa_keys):
        return "KASA"
    
    # Durum / Açık işlemler (ÖNCELİKLİ — "ne durumda" PNL'den önce yakala)
    durum_keys = ["durum", "açık işlem", "pozisyon", "ne açık", "açıkta ne var", "işlemler"]
    if any(k in m for k in durum_keys):
        return "DURUM"
    
    # Kar / Zarar soruları
    pnl_keys = ["kar", "zarar", "kazandık", "kaybettik", "kâr", "kayıp", "profit", "loss", 
                 "pnl", "kazanç", "performans", "nasıl gidiyor"]
    if any(k in m for k in pnl_keys):
        return "PNL"
    
    # Coin analiz soruları
    analiz_keys = ["ne olur", "ne olcak", "ne olacak", "nasıl", "yükselir", "düşer",
                   "alınır mı", "alayım mı", "girilir mi", "fırsat", "analiz", "tahmin",
                   "görüşün", "ne dersin", "ne düşünüyorsun", "durumu", "nereye gider",
                   "hedef", "long", "short", "al", "sat"]
    if any(k in m for k in analiz_keys):
        return "ANALIZ"
    
    # Genel selamlama
    selam_keys = ["selam", "merhaba", "nbr", "naber", "hey", "sa", "günaydın", "iyi geceler"]
    if any(k in m for k in selam_keys):
        return "SELAM"
        
    # Kaldıraç soruları
    kaldirac_keys = ["kaldıraç", "kaldırac", "kaldıraçlı", "x aç", "kaç x", "kac x", "kaldirac", "kaldıraçlı gireyim mi"]
    if any(k in m for k in kaldirac_keys):
        return "KALDIRAC"
    
    return "BILINMEYEN"

# ─── KALDIRAÇ HESAPLAMA ───
def kaldirac_hesapla(guven_skoru, atr_degeri, anlik_fiyat):
    """Volatilite (ATR) ve AI Güven Skoruna göre güvenli izole kaldıraç önerisi."""
    atr_yuzde = (atr_degeri / anlik_fiyat) * 100
    stop_yuzde = atr_yuzde * 1.5
    if stop_yuzde == 0: stop_yuzde = 1.0 
    
    maks_guvenli_kaldirac = (100 / stop_yuzde) / 2.0
    guven_carpani = guven_skoru / 50.0  
    oneri = maks_guvenli_kaldirac * guven_carpani
    oneri = max(1.0, min(20.0, oneri))
    
    if oneri < 3: return "1x - 2x (Düşük)", 2
    elif oneri < 5: return "3x - 5x (Dikkatli)", 4
    elif oneri < 10: return "5x - 8x (Normal)", 7
    elif oneri < 15: return "10x - 12x (İdeal)", 10
    else: return "15x - 20x (Agresif)", 15


# ─── KASA RAPORU ───
def _kasa_raporu():
    from modules import paper_trader
    from config import PAPER_TRADING, PAPER_START_BALANCE, TOPLAM_BAKIYE
    
    if PAPER_TRADING:
        bakiye = paper_trader.get_balance(PAPER_START_BALANCE)
        baslangic = PAPER_START_BALANCE
        acik_pozisyon = paper_trader.count_open_positions()
    else:
        bakiye = TOPLAM_BAKIYE
        baslangic = TOPLAM_BAKIYE
        acik_pozisyon = len(islemleri_yukle())
    
    fark = bakiye - baslangic
    yuzde = (fark / baslangic) * 100 if baslangic > 0 else 0
    emoji = "📈" if fark >= 0 else "📉"
    
    
    mesaj = f"💰 <b>KASA DURUMU</b>\n\n"
    mesaj += f"Başlangıç: <code>${baslangic:.2f}</code>\n"
    mesaj += f"Güncel Bakiye: <code>${bakiye:.2f}</code>\n"
    mesaj += f"{emoji} Toplam: <code>{'+'if fark>=0 else ''}{fark:.2f}$</code> (%{yuzde:.1f})\n"
    mesaj += f"📊 Açık Pozisyon: <code>{acik_pozisyon}</code>"
    return mesaj


# ─── KAR/ZARAR RAPORU ───
def _pnl_raporu():
    from modules import paper_trader
    
    mesaj = ""
    
    # Günlük performans
    gunluk = paper_trader.build_performance_report(1)
    if gunluk:
        mesaj += gunluk + "\n\n"
    
    # Haftalık performans
    haftalik = paper_trader.build_performance_report(7)
    if haftalik:
        mesaj += haftalik
    
    if not mesaj:
        mesaj = "📊 Henüz kapanmış işlem yok, performans verisi oluşmadı."
    
    return mesaj


# ─── COİN ANALİZİ ───
def _coin_analizi(sembol):
    try:
        from modules.teknik import kapsamli_veri_cek
        from modules.ai_model import yapay_zeka_analizi
        
        data = kapsamli_veri_cek(sembol)
        if data is None or data.empty:
            return f"⚠️ {sembol} için veri çekilemedi. Biraz sonra tekrar dene."
        
        karar, guven_skoru, yon = yapay_zeka_analizi(data, sembol)
        son = data.iloc[-1]
        fiyat = son['close']
        atr = son.get('atr', fiyat * 0.03) # Eksikse varsayılan
        kaldirac_onerisi_str, secilen_kaldirac = kaldirac_hesapla(guven_skoru, atr, fiyat)
        
        supertrend = "Yukarı 🟢" if son.get('supertrend_sinyal', 0) == 1 else "Aşağı 🔴"
        stoch = son.get('stoch_rsi_k', 0)
        squeeze = "EVET 💥" if son.get('bb_squeeze', 0) == 1 else "Hayır"
        ema_skor = son.get('ema_ribbon_skor', 0)
        funding = son.get('funding_rate', 0)
        trend_4h = "Yukarı 🟢" if son.get('trend_4h_yukari', 0) == 1 else "Aşağı 🔴"
        
        # PnL Simülasyonu (100$ Marjin ile)
        stop_yuzdesi = ((atr * 1.5) / fiyat) * 100
        hedef_yuzdesi = ((atr * 3.0) / fiyat) * 100
        zarar_roe = stop_yuzdesi * secilen_kaldirac
        kar_roe = hedef_yuzdesi * secilen_kaldirac
        zarar_dolar = 100 * (zarar_roe / 100)
        kar_dolar = 100 * (kar_roe / 100)
        
        # Yorum oluştur
        if yon == "LONG" and guven_skoru >= 55:
            yorum = "🚀 Güçlü AL sinyali var! Trend ve momentum uyumlu."
        elif yon == "LONG":
            yorum = "📗 Hafif pozitif. Yukarı potansiyel var ama çok güçlü değil."
        elif yon == "SHORT" and guven_skoru <= 40:
            yorum = "⛔ Güçlü SAT sinyali. Aşağı baskı hakim."
        elif yon == "SHORT":
            yorum = "📕 Hafif negatif. Dikkatli ol, düşüş riski var."
        else:
            yorum = "⏳ Şu an net sinyal yok. Beklemekte fayda var."
        
        # Funding rate yorumu
        if funding > 0.3:
            funding_yorum = "⚠️ Funding yüksek — long kalabalık, dikkat!"
        elif funding < -0.3:
            funding_yorum = "⚠️ Funding negatif — short kalabalık, sıkışma gelebilir!"
        else:
            funding_yorum = "✅ Funding normal."
            
        # RL Strateji Önerisi
        try:
            from modules.rl_agent import get_rl_strategy
            volatilite = atr / fiyat if fiyat > 0 else 0
            imbalance = son.get('order_book_imbalance', 0.5)
            rl_strat = get_rl_strategy(guven_skoru, yon, volatilite, imbalance)
        except Exception as e:
            rl_strat = {"dca": "Tek Seferde Tam Miktar Gir (%100)", "tp_sl": "Sabit TP/SL Kullan"}
        
        mesaj = f"🔍 <b>{sembol} ANALİZ</b>\n\n"
        mesaj += f"💵 Fiyat: <code>{fiyat:.4f}</code>\n"
        mesaj += f"🤖 AI Karar: <b>{karar}</b> ({yon})\n"
        mesaj += f"📊 Güven: <code>%{guven_skoru:.1f}</code>\n"
        mesaj += f"⚖️ <b>Önerilen Kaldıraç:</b> <code>{kaldirac_onerisi_str}</code>\n"
        mesaj += f"🧠 <b>Yapay Zeka (RL) İşlem Stratejisi:</b>\n"
        mesaj += f"   • Giriş: <code>{rl_strat['dca']}</code>\n"
        mesaj += f"   • Yönetim: <code>{rl_strat['tp_sl']}</code>\n\n"
        mesaj += f"📈 SuperTrend: {supertrend}\n"
        mesaj += f"⏰ 4H Trend: {trend_4h}\n"
        mesaj += f"💫 Stoch RSI: <code>{stoch:.2f}</code>\n"
        mesaj += f"🎯 EMA Gücü: <code>{ema_skor:.2f}</code>\n"
        mesaj += f"💥 Squeeze: {squeeze}\n"
        mesaj += f"💰 Funding: <code>{funding:.3f}</code> — {funding_yorum}\n\n"
        mesaj += f"💡 <b>{secilen_kaldirac}x Kaldıraç & 100$ İşlem Örneği:</b>\n"
        mesaj += f"🔴 Stop Olunursa: <code>-${zarar_dolar:.1f}</code> (%-{zarar_roe:.1f})\n"
        mesaj += f"🟢 Hedef Gelirse: <code>+${kar_dolar:.1f}</code> (+%{kar_roe:.1f})\n\n"
        mesaj += f"💬 <b>Yorum:</b> {yorum}"
        
        return mesaj
    except Exception as e:
        return f"⚠️ {sembol} analiz edilirken hata: {e}"


# ─── AÇIK İŞLEMLER DURUMU ───  
def _durum_raporu():
    if PAPER_TRADING:
        from modules import paper_trader
        state = paper_trader._load_state() if hasattr(paper_trader, "_load_state") else {"positions": {}}
        all_positions = state.get("positions", {})
        getir = {}
        for sembol, p in all_positions.items():
            # SADECE AÇIK İŞLEMLERİ GÖSTER! (Kapalılar listeyi uzatıp Telegram'ı çökertmesin)
            if p.get("status") not in ["OPEN", "TP1"]:
                continue
            
            anlik_fiyat = p.get("entry")
            try:
                # PnL'in %0 görünmemesi için güncel fiyatı Binance'den hızlıca çekelim
                r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sembol}", timeout=2)
                if r.ok:
                    anlik_fiyat = float(r.json()["price"])
            except:
                pass
                
            getir[sembol] = {
                "giris_fiyat": p.get("entry"),
                "anlik_fiyat": anlik_fiyat,
                "ai_guven": p.get("ai_guven", 0),
                "side": p.get("side", "LONG"),
            }
        islemler = getir
    else:
        islemler = islemleri_yukle()
        for sembol, detay in islemler.items():
            try:
                r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sembol}", timeout=2)
                if r.ok:
                    detay["anlik_fiyat"] = float(r.json()["price"])
            except:
                pass
    
    if not islemler:
        return "ℹ️ Şu an açıkta bir işlem bulunmuyor. Pusuya devam! 🐾"

    rapor = "📊 <b>AÇIK İŞLEMLER</b>\n\n"
    for sembol, detay in islemler.items():
        giris = detay.get('giris_fiyat', 0)
        anlik = detay.get('anlik_fiyat', giris) 
        side = detay.get('side', 'LONG')
        
        kar_zarar = 0.0
        if giris > 0:
            if side == "LONG":
                kar_zarar = ((anlik - giris) / giris) * 100
            else:
                kar_zarar = ((giris - anlik) / giris) * 100
        
        ham_guven = detay.get('ai_guven', 0)
        
        emoji = "🟢" if kar_zarar >= 0 else "🔴"
        rapor += f"{emoji} <b>{sembol}</b> ({side})\n"
        rapor += f"   💰 Giriş: <code>{giris:.4f}</code>\n"
        rapor += f"   📈 Güncel: <code>{anlik:.4f}</code>\n"
        rapor += f"   📊 PnL: <code>{'+'if kar_zarar>=0 else ''}{kar_zarar:.2f}%</code>\n"
        if ham_guven > 0:
            rapor += f"   🤖 AI Güven: <code>%{ham_guven}</code>\n"
        rapor += "-" * 26 + "\n"

    return rapor


# ============================================================
#  KOMUT HANDLERLERİ
# ============================================================

# /durum komutu (mevcut)
async def durum_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    await update.message.reply_text(_durum_raporu(), parse_mode="HTML")

# /kasa komutu
async def kasa_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    await update.message.reply_text(_kasa_raporu(), parse_mode="HTML")

# /pnl komutu
async def pnl_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    await update.message.reply_text(_pnl_raporu(), parse_mode="HTML")

# /analiz BTC komutu
async def analiz_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    
    if context.args:
        sembol_text = " ".join(context.args)
        sembol = _sembol_bul(sembol_text)
        if not sembol:
            sembol = sembol_text.upper()
            if not sembol.endswith("USDT"):
                sembol += "USDT"
    else:
        sembol = "BTCUSDT"
    
    await update.message.reply_text(f"⏳ {sembol} analiz ediliyor, bekle...", parse_mode="HTML")
    sonuc = _coin_analizi(sembol)
    await update.message.reply_text(sonuc, parse_mode="HTML")

# /rapor BTC komutu (eski asistan.py'den — PRO İŞLEM PLANI)
async def rapor_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ Örnek: <code>/rapor BTCUSDT</code>", parse_mode="HTML")
        return
    
    sembol_text = " ".join(context.args)
    sembol = _sembol_bul(sembol_text)
    if not sembol:
        sembol = sembol_text.upper()
        if not sembol.endswith("USDT"):
            sembol += "USDT"
    
    await update.message.reply_text(f"⏳ <b>{sembol}</b> İşlem Planı Hazırlanıyor...", parse_mode="HTML")
    
    try:
        from modules.teknik import kapsamli_veri_cek
        from modules.ai_model import yapay_zeka_analizi
        from modules.news import haber_duyarliligi
        from config import TOPLAM_BAKIYE, ISLEM_BASI_RISK
        
        data = kapsamli_veri_cek(sembol)
        if data is None or data.empty:
            await update.message.reply_text(f"❌ <b>{sembol}</b> verisi çekilemedi.", parse_mode="HTML")
            return
        
        karar, guven_skoru, yon = yapay_zeka_analizi(data, sembol)
        haber_skoru = haber_duyarliligi(sembol)
        
        anlik_fiyat = data.iloc[-1]['close']
        atr_degeri = data.iloc[-1]['atr']
        
        stop_loss = anlik_fiyat - (atr_degeri * 1.5)
        hedef_kar_1 = anlik_fiyat + (atr_degeri * 1.5)
        hedef_kar_2 = anlik_fiyat + (atr_degeri * 3.0)
        
        dinamik_risk_carpani = 1.0
        if guven_skoru > 80: dinamik_risk_carpani += 0.5
        elif guven_skoru > 70: dinamik_risk_carpani += 0.2
        elif guven_skoru < 60: dinamik_risk_carpani -= 0.5
        
        makro = data.iloc[-1].get('makro_risk_skoru', 0.5)
        fng = data.iloc[-1].get('korku_acgozluluk_endeksi', 0.5)
        if makro < 0.3: dinamik_risk_carpani -= 0.5
        if fng > 0.8: dinamik_risk_carpani -= 0.3
        elif fng < 0.2: dinamik_risk_carpani += 0.2
        
        dinamik_risk_carpani = max(0.25, min(2.5, dinamik_risk_carpani))
        
        riske_atilan_tutar = TOPLAM_BAKIYE * (ISLEM_BASI_RISK * dinamik_risk_carpani)
        stop_mesafesi = abs(anlik_fiyat - stop_loss)
        alinacak_adet = riske_atilan_tutar / max(stop_mesafesi, 1e-9)
        kaldirac_onerisi_str, _ = kaldirac_hesapla(guven_skoru, atr_degeri, anlik_fiyat)
        
        rapor_metni = f"""
🚀 <b>PRO İŞLEM PLANI: {sembol}</b>
───────────────────────
📊 <b>STRATEJİ:</b> {karar} ({yon}) (%{guven_skoru:.1f})
📰 <b>HABER PUANI:</b> {haber_skoru}

💰 <b>CÜZDAN YÖNETİMİ (Akıllı Mod)</b>
🏦 <b>Kasa:</b> {TOPLAM_BAKIYE} | <b>Riske Edilen:</b> %{(ISLEM_BASI_RISK * dinamik_risk_carpani * 100):.1f}
🛒 <b>Alınacak Adet:</b> <code>{alinacak_adet:.4f}</code>
💸 <b>Max Zarar:</b> -${riske_atilan_tutar:.2f}
💵 <b>Pozisyon Büyüklüğü:</b> ${(alinacak_adet * anlik_fiyat):.2f}
⚖️ <b>Önerilen Kaldıraç:</b> <code>{kaldirac_onerisi_str}</code>
───────────────────────
📥 <b>GİRİŞ:</b> {anlik_fiyat:.2f}
🛑 <b>STOP-LOSS:</b> {stop_loss:.2f}
🎯 <b>HEDEF 1:</b> {hedef_kar_1:.2f}
🚀 <b>HEDEF 2:</b> {hedef_kar_2:.2f}
───────────────────────
🛡️ <i>Stop olursan sadece {riske_atilan_tutar:.1f}$ kaybedersin.</i>
"""
        await update.message.reply_text(rapor_metni, parse_mode="HTML")
        
    except Exception as e:
        await update.message.reply_text(f"⚠️ Hata: {e}", parse_mode="HTML")


# ─── YARDIMCI KAPATMA FONKSİYONU ───
async def _kapat_islem_fonksiyonu(update, context, sembol):
    """Hem komut hem de buton için ortak kapatma mantığı."""
    chat_id = update.effective_chat.id
    
    # Buton mu komut mu kontrol et (Mesaj göndermek için)
    is_callback = update.callback_query is not None
    sender = update.callback_query if is_callback else update.message

    await context.bot.send_message(chat_id=chat_id, text=f"⏳ <b>{sembol}</b> işlemi anlık fiyattan kapatılıyor...", parse_mode="HTML")
    
    try:
        # Binance'den anlık fiyatı çek
        r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sembol}", timeout=5)
        if not r.ok:
            await context.bot.send_message(chat_id=chat_id, text="❌ Fiyat çekilemedi, işlem iptal edildi.", parse_mode="HTML")
            return
        anlik_fiyat = float(r.json()["price"])
        
        if PAPER_TRADING:
            from modules import paper_trader
            pos = paper_trader.get_open_position(sembol)
            if not pos:
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ <b>{sembol}</b> için açıkta sanal (paper) işlem bulunamadı.", parse_mode="HTML")
                return
                
            now_iso = datetime.now().isoformat()
            msg, event_data = paper_trader.close_position(sembol, anlik_fiyat, now_iso, "MANUEL")
            if msg:
                # msg HTML formatında gelmiyor olabilir, paper_trader'dan gelen mesajı kontrol etmeliyim
                # Ama şimdilik burada manuel gönderdiklerimi HTML yapıyorum
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                # Kapanış varsa veritabanını da güncelle
                if event_data and event_data.get("type") == "CLOSE":
                    islem_kapat(sembol, anlik_fiyat, event_data.get("pnl", 0))
            else:
                await context.bot.send_message(chat_id=chat_id, text="❌ İşlem kapatılamadı.", parse_mode="HTML")
        else:
            # Gerçek işlem kapatma mantığı (Aynı kalıyor)
            from modules.database import acik_islemleri_yukle
            islemler = acik_islemleri_yukle()
            if sembol not in islemler:
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ <b>{sembol}</b> için açık bir işlem bulunamadı.", parse_mode="HTML")
                return
                
            detay = islemler[sembol]
            giris = detay.get('giris_fiyat', 0)
            side = detay.get('side', 'LONG')
            kar_zarar = ((anlik_fiyat - giris) / giris) * 100 if side == "LONG" else ((giris - anlik_fiyat) / giris) * 100
            
            islem_kapat(sembol, anlik_fiyat, kar_zarar)
            emoji = "🟢" if kar_zarar >= 0 else "🔴"
            await context.bot.send_message(chat_id=chat_id, text=f"📤 <b>İŞLEM MANUEL KAPATILDI</b> — {sembol}\nGiriş: <code>{giris:.4f}</code> | Çıkış: <code>{anlik_fiyat:.4f}</code>\nPnL: {emoji} <code>{'+' if kar_zarar>=0 else ''}{kar_zarar:.2f}%</code>", parse_mode="HTML")
            
            if not PAPER_TRADING:
                try:
                    from modules.binance_trader import gercek_islem_kapat
                    gercek_islem_kapat(sembol)
                    await context.bot.send_message(chat_id=chat_id, text="✅ Binance üzerindeki pozisyon ve bekleyen emirler de başarıyla iptal edildi.", parse_mode="HTML")
                except Exception as e:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Veritabanı kapatıldı ancak Binance emir iptalinde hata: {e}", parse_mode="HTML")
            
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata oluştu: {e}", parse_mode="HTML")


# ─── MANUEL İŞLEM KAPATMA KOMUTU ───
async def kapat_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ Örnek kullanım: <code>/kapat BTC</code> veya <code>/kapat BTCUSDT</code>", parse_mode="HTML")
        return
        
    sembol_text = " ".join(context.args)
    sembol = _sembol_bul(sembol_text)
    if not sembol:
        sembol = sembol_text.upper()
        if not sembol.endswith("USDT"):
            sembol += "USDT"
            
    await _kapat_islem_fonksiyonu(update, context, sembol)


# ─── DOĞAL DİL SOHBET (en önemli kısım) ───
async def sohbet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Her türlü mesajı anla ve cevap ver."""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    
    metin = update.message.text
    if not metin:
        return
    
    niyet = _niyet_bul(metin)
    
    if niyet == "SELAM":
        await update.message.reply_text(
            "Selam Sıla! 👋 Ben BorsaAI, kripto asistanın.\n\n"
            "Bana şunları sorabilirsin:\n"
            "• <code>BTC ne olacak?</code> → Anlık analiz\n"
            "• <code>Kasada ne var?</code> → Bakiye\n"
            "• <code>Kar ettik mi?</code> → Performans\n"
            "• <code>Açık işlemler?</code> → Pozisyon durumu\n"
            "• Herhangi bir coin adı yaz → Analiz",
            parse_mode="HTML"
        )
        return
    
    if niyet == "KASA":
        await update.message.reply_text(_kasa_raporu(), parse_mode="HTML")
        return
    
    if niyet == "PNL":
        await update.message.reply_text(_pnl_raporu(), parse_mode="HTML")
        return
    
    if niyet == "DURUM":
        await update.message.reply_text(_durum_raporu(), parse_mode="HTML")
        return
    
    if niyet == "KALDIRAC":
        sembol = _sembol_bul(metin)
        if sembol:
            await update.message.reply_text(f"⏳ <b>{sembol}</b> için kaldıraç analizi yapıyorum... Gaza gelme Sıla, önce veriye bakalım!", parse_mode="HTML")
            sonuc = _coin_analizi(sembol)
            await update.message.reply_text(sonuc, parse_mode="HTML")
        else:
            await update.message.reply_text(
                "🚀 <b>Aç tabi Sıla, yapıştır kaldıracı!</b> Sen mi korkacaksın piyasadan? 💸\n\n"
                "Şaka bir yana, hangi coine kaldıraç girmek istediğini yaz ki sana ideal kaldıraç seviyesini (x) hesaplayayım!\n"
                "*(Örnek: BTC kaldıraçlı girilir mi?)*",
                parse_mode="HTML"
            )
        return
    
    if niyet == "ANALIZ":
        sembol = _sembol_bul(metin)
        if not sembol:
            # Belki sadece coin adı yazmıştır
            await update.message.reply_text(
                "🤔 Hangi coini analiz edeyim? Örnek:\n"
                "• <code>BTC ne olacak?</code>\n"
                "• <code>ETH durumu nasıl?</code>\n"
                "• <code>SOL alınır mı?</code>",
                parse_mode="HTML"
            )
            return
        
        await update.message.reply_text(f"⏳ {sembol} analiz ediliyor...", parse_mode="HTML")
        sonuc = _coin_analizi(sembol)
        await update.message.reply_text(sonuc, parse_mode="HTML")
        return
    
    # BILINMEYEN — coin ismi olabilir mi kontrol et
    sembol = _sembol_bul(metin)
    if sembol:
        await update.message.reply_text(f"⏳ {sembol} analiz ediliyor...", parse_mode="HTML")
        sonuc = _coin_analizi(sembol)
        await update.message.reply_text(sonuc, parse_mode="HTML")
        return
    
    # Gerçekten anlamadıysa
    await update.message.reply_text(
        "🤖 Anlamadım Sıla, bana şunları sorabilirsin:\n\n"
        "• Bir coin yaz → <code>BTC</code>, <code>ETH durumu</code>, <code>SOL alınır mı?</code>\n"
        "• <code>Kasada ne var?</code> → Bakiye\n"
        "• <code>Kar ettik mi?</code> → Performans\n"
        "• <code>Açık işlemler?</code> → Pozisyon durumu",
        parse_mode="HTML"
    )


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buton tıklamalarını yönetir."""
    query = update.callback_query
    await query.answer() # Buton tıklamasını onayla (loading simgesini kaldırır)
    
    data = query.data
    chat_id = update.effective_chat.id
    
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return

    if data.startswith("analiz_"):
        sembol = data.replace("analiz_", "")
        await query.edit_message_text(text=f"⏳ <b>{sembol}</b> analiz ediliyor...", parse_mode="HTML")
        sonuc = _coin_analizi(sembol)
        # Analiz sonucunu yeni bir mesaj olarak gönder
        await context.bot.send_message(chat_id=chat_id, text=sonuc, parse_mode="HTML")
        
    elif data.startswith("kapat_"):
        sembol = data.replace("kapat_", "")
        # Manuel kapatma fonksiyonunu doğrudan çağır (Context bağımsız)
        await _kapat_islem_fonksiyonu(update, context, sembol)

# ============================================================
#  SERVİSİ BAŞLATMA
# ============================================================
def telegram_dinleyici_baslat():
    """Thread-safe Telegram dinleyici — kendi event loop'unu oluşturur."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Slash komutları
    application.add_handler(CommandHandler("durum", durum_komutu))
    application.add_handler(CommandHandler("kasa", kasa_komutu))
    application.add_handler(CommandHandler("pnl", pnl_komutu))
    application.add_handler(CommandHandler("analiz", analiz_komutu))
    application.add_handler(CommandHandler("rapor", rapor_komutu))
    application.add_handler(CommandHandler("kapat", kapat_komutu))
    
    # Buton tıklama dinleyicisi
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    
    # Doğal dil sohbet (tüm metin mesajları)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sohbet_handler))
    
    print("✅ Telegram Dinleyici aktif!")
    print("   /durum — Açık işlemler")
    print("   /kasa — Bakiye durumu")
    print("   /pnl — Kar/Zarar raporu")
    print("   /analiz BTC — Coin analizi")
    print("   /rapor BTC — PRO İşlem Planı")
    print("   /kapat BTC — Açık işlemi manuel kapatır")
    print("   Veya doğal dille sohbet et! 💬")

    async def _run():
        async with application:
            await application.start()
            await application.updater.start_polling(drop_pending_updates=True)
            # Sonsuza kadar bekle (thread daemon olduğu için ana süreç bitince kapanır)
            stop_event = asyncio.Event()
            await stop_event.wait()

    try:
        loop.run_until_complete(_run())
    except Exception as e:
        print(f"[Telegram] Dinleyici hatası: {e}")
    finally:
        loop.close()