import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime

# Proje dizinini ekle
sys.path.append(os.getcwd())

from modules.teknik import kapsamli_veri_cek
from modules.ai_model import yapay_zeka_analizi

# --- AYARLAR ---
KOMISYON = 0.0004 # %0.04 (Binance Vadeli İşlem Ortalama Komisyonu)
BASLANGIC_KASA = 1000.0
ISLEM_BASI_RISK = 0.02 # %2

def backtest_v2(sembol):
    print(f"\n🚀 {sembol} için gelişmiş backtest başlıyor...")
    df = kapsamli_veri_cek(sembol)
    if df is None: return

    kasa = BASLANGIC_KASA
    islemler = []
    aktif_pozisyon = None
    
    # AI kararlarını önceden hesaplamak yavaş olabilir, 
    # bu yüzden her 5 mumda bir karar aldığımızı varsayalım (veya her mumda)
    print("🤖 AI kararları simüle ediliyor (bu biraz sürebilir)...")
    
    # Geçmiş performansı en iyi şekilde görmek için 1000 mumu (yaklaşık 10 günlük veri) test edelim
    mum_sayisi = min(len(df), 1000)
    test_range = range(len(df) - mum_sayisi, len(df))
    
    for i in test_range:
        if i > 0 and i % 100 == 0:
            print(f"🔄 İlerleme: {i}/{len(df)} mum analiz edildi...")
            
        current_data = df.iloc[:i+1]
        last_row = df.iloc[i]
        fiyat = last_row['close']
        zaman = df.index[i]
        
        # 1. Pozisyon Kapatma Kontrolü (TP/SL/Trailing)
        if aktif_pozisyon:
            hi, lo = last_row['high'], last_row['low']
            side = aktif_pozisyon['side']
            entry = aktif_pozisyon['entry']
            stop = aktif_pozisyon['stop']
            tp1, tp2 = aktif_pozisyon['tp1'], aktif_pozisyon['tp2']
            atr = aktif_pozisyon['atr']
            
            # Trailing & BE Güncelleme (AGRESİF KÂR KORUMA)
            if side == "LONG":
                if hi > aktif_pozisyon['highest']: aktif_pozisyon['highest'] = hi
                # BE: 1.2 ATR kâra geçince Stop'u komisyonu kurtaracak kadar yukarı çek (Erken Koruma)
                if not aktif_pozisyon['be'] and hi >= entry + (atr * 1.2):
                    aktif_pozisyon['stop'] = entry + (entry * KOMISYON * 2)
                    aktif_pozisyon['be'] = True
                # Dinamik Trailing: 1.8 ATR kârda, stop'u tepeden 1.0 ATR geriden takip ettir
                if hi >= entry + (atr * 1.8):
                    new_stop = aktif_pozisyon['highest'] - atr
                    if new_stop > aktif_pozisyon['stop']: aktif_pozisyon['stop'] = new_stop
            else:
                if lo < aktif_pozisyon['lowest']: aktif_pozisyon['lowest'] = lo
                # BE
                if not aktif_pozisyon['be'] and lo <= entry - (atr * 1.2):
                    aktif_pozisyon['stop'] = entry - (entry * KOMISYON * 2)
                    aktif_pozisyon['be'] = True
                # Dinamik Trailing
                if lo <= entry - (atr * 1.8):
                    new_stop = aktif_pozisyon['lowest'] + atr
                    if new_stop < aktif_pozisyon['stop']: aktif_pozisyon['stop'] = new_stop

            # Hit Kontrolü
            exit_price = None
            reason = ""
            
            if side == "LONG":
                if lo <= aktif_pozisyon['stop']:
                    exit_price = aktif_pozisyon['stop']
                    reason = "SL"
                elif hi >= tp2:
                    exit_price = tp2
                    reason = "TP2"
            else:
                if hi >= aktif_pozisyon['stop']:
                    exit_price = aktif_pozisyon['stop']
                    reason = "SL"
                elif lo <= tp2:
                    exit_price = tp2
                    reason = "TP2"
            
            if exit_price:
                gross_pnl = (exit_price - entry) * aktif_pozisyon['qty'] if side == "LONG" else (entry - exit_price) * aktif_pozisyon['qty']
                exit_comm = exit_price * aktif_pozisyon['qty'] * KOMISYON
                
                # Net PnL = Brüt Kâr - Giriş Komisyonu - Çıkış Komisyonu
                net_pnl = gross_pnl - aktif_pozisyon['entry_comm'] - exit_comm
                kasa += (gross_pnl - exit_comm)
                
                islemler.append({
                    "sembol": sembol, "side": side, "giris": entry, "cikis": exit_price,
                    "net_pnl": net_pnl, "kasa": kasa, "sebep": reason, "zaman": zaman
                })
                aktif_pozisyon = None

        # 2. Yeni Pozisyon Açma Kontrolü
        if not aktif_pozisyon:
            # Her mumda AI'ya sormak çok yavaş, basit bir filtre + AI yapalım
            # Sadece trend yönünde ve momentum varsa sor
            if abs(last_row['rsi'] - 50) > 10 and last_row.get('adx', 0) > 22: # Daha Sert Momentum ve ADX (22)
                try:
                    karar, guven, yon = yapay_zeka_analizi(current_data, sembol)
                    
                    # Kusursuz Keskin Nişancı: AI %60 Güven + EMA50/EMA200 Onayı + SuperTrend
                    st = last_row['supertrend_sinyal']
                    ema200 = last_row.get('ema200', last_row['close'])
                    ema50 = last_row.get('ema50', last_row['close'])
                    
                    is_long = (yon == "LONG" and guven >= 60.0 and st == 1 and fiyat > ema200 and fiyat > ema50)
                    is_short = (yon == "SHORT" and guven <= 40.0 and st == -1 and fiyat < ema200 and fiyat < ema50)
                    
                    if is_long or is_short:
                        side = yon
                        atr = last_row['atr']
                        
                        risk_miktari = kasa * ISLEM_BASI_RISK
                        stop_mesafe = atr * 1.8 # İğnelerden korunmak için zarar mesafesi 1.8 ATR'ye çıkarıldı
                        qty = risk_miktari / stop_mesafe
                        
                        entry_price = fiyat
                        stop_price = entry_price - stop_mesafe if side == "LONG" else entry_price + stop_mesafe
                        tp1_price = entry_price + (atr * 2.0) if side == "LONG" else entry_price - (atr * 2.0)
                        tp2_price = entry_price + (atr * 4.0) if side == "LONG" else entry_price - (atr * 4.0)
                        
                        # Giriş Komisyonu
                        entry_comm = entry_price * qty * KOMISYON
                        kasa -= entry_comm
                        
                        aktif_pozisyon = {
                            "side": side, "entry": entry_price, "qty": qty,
                            "stop": stop_price, "tp1": tp1_price, "tp2": tp2_price,
                            "highest": entry_price, "lowest": entry_price, "be": False,
                            "atr": atr, "entry_comm": entry_comm
                        }
                except:
                    continue

    # Rapor
    if not islemler:
        print("❌ Test süresince hiç işlem açılmadı.")
        return

    df_res = pd.DataFrame(islemler)
    
    # Gerçek metrikler (2$'dan az kârlar komisyon kurtarması/BE sayılır)
    gercek_kazanc = len(df_res[df_res['net_pnl'] > 2.0])
    basa_bas = len(df_res[(df_res['net_pnl'] >= -2.0) & (df_res['net_pnl'] <= 2.0)])
    zarar = len(df_res[df_res['net_pnl'] < -2.0])
    win_rate = (gercek_kazanc / len(df_res)) * 100
    
    kar_oran = ((kasa - BASLANGIC_KASA) / BASLANGIC_KASA) * 100
    
    print("="*50)
    print(f"📊 BACKTEST SONUCU: {sembol}")
    print(f"💰 Başlangıç: ${BASLANGIC_KASA:.2f}")
    print(f"📈 Bitiş: ${kasa:.2f} ({'+' if kar_oran>=0 else ''}{kar_oran:.2f}%)")
    print(f"🎯 Net Başarı (Gerçek Kâr): %{win_rate:.2f}")
    print(f"🔄 Dağılım: {gercek_kazanc} Kâr | {basa_bas} Başa Baş | {zarar} Zarar")
    print("="*50)

if __name__ == "__main__":
    # Farklı stratejilerin coinler üzerindeki etkisini görmek için liste
    test_listesi = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for coin in test_listesi:
        backtest_v2(coin)
