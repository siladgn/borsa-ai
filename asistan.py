import telebot
from config import TELEGRAM_TOKEN, TOPLAM_BAKIYE, ISLEM_BASI_RISK
from modules.teknik import kapsamli_veri_cek
from modules.ai_model import yapay_zeka_analizi
from modules.news import haber_duyarliligi
import warnings

warnings.filterwarnings('ignore')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['rapor', 'analiz'])
def rapor_gonder(message):
    komut = message.text.split()
    if len(komut) < 2:
        bot.reply_to(message, "⚠️ Örnek: `/rapor BTCUSDT`", parse_mode="Markdown")
        return
        
    sembol = komut[1].upper()
    bekleme_mesaji = bot.reply_to(message, f"⏳ *{sembol}* ve Cüzdan Analizi Yapılıyor...", parse_mode="Markdown")
    
    try:
        data = kapsamli_veri_cek(sembol)
        if data is None or data.empty:
            bot.edit_message_text(f"❌ *{sembol}* bulunamadı.", chat_id=message.chat.id, message_id=bekleme_mesaji.message_id)
            return

        karar, guven_skoru, yon = yapay_zeka_analizi(data)
        haber_skoru = haber_duyarliligi(sembol)
        
        # --- RİSK VE POZİSYON HESAPLARI ---
        anlik_fiyat = data.iloc[-1]['close']
        atr_degeri = data.iloc[-1]['atr']
        
        stop_loss = anlik_fiyat - (atr_degeri * 1.5)
        hedef_kar_1 = anlik_fiyat + (atr_degeri * 1.5)
        hedef_kar_2 = anlik_fiyat + (atr_degeri * 3.0)

        # POZİSYON BÜYÜKLÜĞÜ HESABI (Akıllı Kasa - Kelly)
        dinamik_risk_carpani = 1.0
        
        # 1. YZ Güveni Puanı Etkisi
        if guven_skoru > 80: dinamik_risk_carpani += 0.5
        elif guven_skoru > 70: dinamik_risk_carpani += 0.2
        elif guven_skoru < 60: dinamik_risk_carpani -= 0.5
        
        # 2. Makro Risk ve Duygu Etkisi
        makro = data.iloc[-1].get('makro_risk_skoru', 0.5)
        fng = data.iloc[-1].get('korku_acgozluluk_endeksi', 0.5)
        
        if makro < 0.3: dinamik_risk_carpani -= 0.5
        if fng > 0.8: dinamik_risk_carpani -= 0.3
        elif fng < 0.2: dinamik_risk_carpani += 0.2
        
        dinamik_risk_carpani = max(0.25, min(2.5, dinamik_risk_carpani))
        
        riske_atilan_tutar = TOPLAM_BAKIYE * (ISLEM_BASI_RISK * dinamik_risk_carpani)
        stop_mesafesi = anlik_fiyat - stop_loss
        alinacak_adet = riske_atilan_tutar / stop_mesafesi

        rapor_metni = f"""
🚀 *PRO İŞLEM PLANI: {sembol}*
───────────────────────
📊 *STRATEJİ:* {karar} ({yon}) (%{guven_skoru:.1f})
📰 *HABER PUANI:* {haber_skoru}

 *CÜZDAN YÖNETİMİ (Akıllı Mod)*
🏦 *Kasa:* {TOPLAM_BAKIYE} | *Riske Edilen:* %{(ISLEM_BASI_RISK * dinamik_risk_carpani * 100):.1f}
🛒 *Alınacak Adet:* `{alinacak_adet:.4f}`
💸 *Durdurulma (Stop) Zararı:* -${riske_atilan_tutar:.2f}
💵 *Toplam Pozisyon Büyüklüğü:* ${(alinacak_adet * anlik_fiyat):.2f}
───────────────────────
📥 *GİRİŞ:* {anlik_fiyat:.2f}
🛑 *STOP-LOSS:* {stop_loss:.2f}
🎯 *HEDEF 1:* {hedef_kar_1:.2f}
🚀 *HEDEF 2:* {hedef_kar_2:.2f}
───────────────────────
🛡️ *Not: Bu adetle girersen, stop olduğunda sadece {riske_atilan_tutar:.1f} birim kaybedersin.*
"""
        bot.edit_message_text(rapor_metni, chat_id=message.chat.id, message_id=bekleme_mesaji.message_id, parse_mode="Markdown")
        
    except Exception as e:
        bot.edit_message_text(f"⚠️ Hata: {e}", chat_id=message.chat.id, message_id=bekleme_mesaji.message_id)

print("🤖 Cüzdan Yönetimli Pro Asistan aktif...")
bot.infinity_polling()