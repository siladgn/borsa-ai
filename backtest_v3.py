import pandas as pd
import numpy as np
import sys
import os
import warnings
from datetime import datetime

# Proje dizinini ekle
sys.path.append(os.getcwd())

from modules.teknik import kapsamli_veri_cek
from modules.ai_model import yapay_zeka_analizi
from modules import mtf_engine, liquidity

warnings.filterwarnings('ignore')

# --- AYARLAR ---
KOMISYON = 0.0004 # %0.04 (Binance Vadeli İşlem Ortalama Komisyonu)
BASLANGIC_KASA = 1000.0
ISLEM_BASI_RISK = 0.02 # %2

def backtest_v3(sembol):
    print(f"\n🚀 {sembol} için DİNAMİK STRATEJİ BACKTEST (v3) başlıyor...")
    df = kapsamli_veri_cek(sembol)
    if df is None: return

    kasa = BASLANGIC_KASA
    islemler = []
    aktif_pozisyon = None
    
    # Test aralığı: Son 500 mum (hızlı test için)
    mum_sayisi = min(len(df), 500)
    test_range = range(len(df) - mum_sayisi, len(df))
    
    print(f"📊 {mum_sayisi} mum analiz edilecek...")

    for i in test_range:
        current_data = df.iloc[:i+1]
        last_row = df.iloc[i]
        anlik_fiyat = last_row['close']
        zaman = df.index[i]
        atr = last_row['atr']
        
        # 1. Pozisyon Kapatma Kontrolü (Dinamik Trailing & TP/SL)
        if aktif_pozisyon:
            hi, lo = last_row['high'], last_row['low']
            side = aktif_pozisyon['side']
            entry = aktif_pozisyon['entry']
            
            # --- DİNAMİK TRAILING STOP (main.py ve paper_trader.py'deki yeni mantık) ---
            if side == "LONG":
                if hi > aktif_pozisyon['highest']: aktif_pozisyon['highest'] = hi
            else:
                if lo < aktif_pozisyon['lowest']: aktif_pozisyon['lowest'] = lo

            # BE (Break-even): %1.2 kârda girişe çek
            be_threshold = 0.012
            if not aktif_pozisyon['be']:
                gain = (hi - entry) / entry if side == "LONG" else (entry - lo) / entry
                if gain >= be_threshold:
                    # Komisyonları da kurtarmak için stop seviyesini komisyon payı kadar kaydır
                    aktif_pozisyon['stop'] = entry + (entry * KOMISYON * 2) if side == "LONG" else entry - (entry * KOMISYON * 2)
                    aktif_pozisyon['be'] = True

            # Trailing: %2.5 kârda başla, %1.2 takip et. Kâr arttıkça daralt.
            gain_max = (aktif_pozisyon['highest'] - entry) / entry if side == "LONG" else (entry - aktif_pozisyon['lowest']) / entry
            ts_dist = 0.010
            if gain_max >= 0.10: ts_dist = 0.005
            elif gain_max >= 0.05: ts_dist = 0.007

            if gain_max >= 0.025:
                if side == "LONG":
                    new_stop = aktif_pozisyon['highest'] * (1 - ts_dist)
                    if new_stop > aktif_pozisyon['stop']: aktif_pozisyon['stop'] = new_stop
                else:
                    new_stop = aktif_pozisyon['lowest'] * (1 + ts_dist)
                    if new_stop < aktif_pozisyon['stop']: aktif_pozisyon['stop'] = new_stop

            # Hit Kontrolü
            exit_price = None
            reason = ""
            if side == "LONG":
                if lo <= aktif_pozisyon['stop']:
                    exit_price = aktif_pozisyon['stop']
                    reason = "SL/TS"
                elif hi >= aktif_pozisyon['tp2']:
                    exit_price = aktif_pozisyon['tp2']
                    reason = "TP2"
            else:
                if hi >= aktif_pozisyon['stop']:
                    exit_price = aktif_pozisyon['stop']
                    reason = "SL/TS"
                elif lo <= aktif_pozisyon['tp2']:
                    exit_price = aktif_pozisyon['tp2']
                    reason = "TP2"
            
            if exit_price:
                pnl = (exit_price - entry) * aktif_pozisyon['qty'] if side == "LONG" else (entry - exit_price) * aktif_pozisyon['qty']
                exit_comm = exit_price * aktif_pozisyon['qty'] * KOMISYON
                net_pnl = pnl - aktif_pozisyon['entry_comm'] - exit_comm
                kasa += (pnl - exit_comm)
                
                islemler.append({
                    "zaman": zaman, "side": side, "giris": entry, "cikis": exit_price,
                    "net_pnl": net_pnl, "kasa": kasa, "sebep": reason
                })
                aktif_pozisyon = None

        # 2. Yeni Pozisyon Açma Kontrolü (Dinamik Filtreler)
        if not aktif_pozisyon:
            # AI Analizi
            karar, guven, yon = yapay_zeka_analizi(current_data, sembol)
            
            # MTF Skorunu doğrudan data üzerinden veya simüle ederek al
            last = current_data.iloc[-1]
            mtf_skor = 0
            if last['close'] > last.get('ema200', last['close']): mtf_skor += 30
            if last['rsi'] > 55: mtf_skor += 20
            if last.get('supertrend_sinyal', 0) == 1: mtf_skor += 30
            if yon == "SHORT": mtf_skor = -mtf_skor

            likidite = liquidity.likidite_bolgeleri_bul(current_data)
            
            # Likidite listesinden en yakın destek ve direnci seç
            destek = anlik_fiyat * 0.98
            direnc = anlik_fiyat * 1.02
            if likidite:
                alt_bolgeler = [b['fiyat'] for b in likidite if b['fiyat'] < anlik_fiyat]
                ust_bolgeler = [b['fiyat'] for b in likidite if b['fiyat'] > anlik_fiyat]
                if alt_bolgeler: destek = max(alt_bolgeler)
                if ust_bolgeler: direnc = min(ust_bolgeler)
            
            # Dinamik Giriş Şartları
            is_long = (yon == "LONG" and guven >= 55 and mtf_skor >= 0)
            is_short = (yon == "SHORT" and guven >= 55 and mtf_skor <= 0)
            
            if is_long or is_short:
                # Dinamik TP/SL Seviyeleri
                atr_carpani_sl = 0.8 if guven > 75 else (1.2 if guven < 55 else 1.0)
                atr_carpani_tp2 = 2.5 if guven > 75 else (1.5 if guven < 55 else 2.0)
                
                if is_long:
                    stop_price = max(anlik_fiyat - (atr * atr_carpani_sl), destek * 0.995)
                    tp2_price = min(anlik_fiyat + (atr * atr_carpani_tp2), direnc * 1.005)
                else:
                    stop_price = min(anlik_fiyat + (atr * atr_carpani_sl), direnc * 1.005)
                    tp2_price = max(anlik_fiyat - (atr * atr_carpani_tp2), destek * 0.995)
                
                # Risk ve Miktar
                risk_tutar = kasa * ISLEM_BASI_RISK
                stop_dist = abs(anlik_fiyat - stop_price)
                if stop_dist == 0: stop_dist = atr * 1.5
                qty = risk_tutar / stop_dist
                
                entry_comm = anlik_fiyat * qty * KOMISYON
                kasa -= entry_comm
                
                aktif_pozisyon = {
                    "side": "LONG" if is_long else "SHORT",
                    "entry": anlik_fiyat, "qty": qty, "stop": stop_price, "tp2": tp2_price,
                    "highest": anlik_fiyat, "lowest": anlik_fiyat, "be": False,
                    "entry_comm": entry_comm, "guven": guven
                }

    # Sonuç Raporu
    if not islemler:
        print(f"❌ {sembol} için test süresince uygun işlem sinyali oluşmadı.")
        return

    res = pd.DataFrame(islemler)
    wins = len(res[res['net_pnl'] > 0])
    win_rate = (wins / len(res)) * 100
    total_pnl = kasa - BASLANGIC_KASA
    
    print("="*50)
    print(f"🏁 TEST BİTTİ: {sembol}")
    print(f"💰 Başlangıç: ${BASLANGIC_KASA:.2f} | Bitiş: ${kasa:.2f}")
    print(f"📊 Net Kar/Zarar: ${total_pnl:.2f} (%{(total_pnl/BASLANGIC_KASA)*100:.2f})")
    print(f"📈 Toplam İşlem: {len(res)} | Win-Rate: %{win_rate:.2f}")
    print(f"🏆 En İyi İşlem: ${res['net_pnl'].max():.2f} | 💀 En Kötü: ${res['net_pnl'].min():.2f}")
    print("="*50)

if __name__ == "__main__":
    for s in ["BTCUSDT", "ETHUSDT"]:
        backtest_v3(s)
