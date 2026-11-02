import pandas as pd
from sklearn.preprocessing import StandardScaler
from modules.teknik import kapsamli_veri_cek
import warnings

warnings.filterwarnings('ignore')

def gercek_backtest(sembol):
    print(f"\n⏳ [{sembol}] İçin MTF Veri Çekiliyor ve Makine Öğrenmesi Testi Başlıyor...")
    data = kapsamli_veri_cek(sembol)
    if data is None or data.empty: return
    df = data.copy()

    # Gelecekteki getiriyi (yüzdesel) ve hedefi (1 veya 0) hesapla
    df["gelecek_getiri"] = df["close"].pct_change().shift(-1)
    df["target"] = (df["gelecek_getiri"] > 0).astype(int)
    df = df.dropna()

    from modules.ai_model import OZELLIKLER as ozellikler, model_egit
    bolme_noktasi = int(len(df) * 0.8)
    
    # Eğitim ve Test verilerini ayırma
    X_egitim = df[ozellikler].iloc[:bolme_noktasi].values
    y_egitim = df["target"].iloc[:bolme_noktasi].values
    X_test = df[ozellikler].iloc[bolme_noktasi:].values
    
    # Gerçek PnL hesabı için test dönemindeki yüzdesel getirileri ayırıyoruz
    gercek_getiri_oranlari = df["gelecek_getiri"].iloc[bolme_noktasi:].values

    # Veri ölçeklendirme (Data Leakage olmadan, eğitim verisine göre)
    scaler = StandardScaler()
    X_egitim_scaled = scaler.fit_transform(X_egitim)
    X_test_scaled = scaler.transform(X_test)

    # Model Eğitimi ve Tahmin Olasılıkları
    model = model_egit(X_egitim_scaled, y_egitim)
    olasiliklar = model.predict_proba(X_test_scaled)[:, 1]

    # --- YENİ EKLENEN GERÇEKÇİ KASA VE KOMİSYON YÖNETİMİ ---
    isleme_girilen_sayi = 0
    basarili_islem = 0
    baslangic_kasa = 1000.0  # 1000 USDT ile teste başlıyoruz
    kasa = baslangic_kasa
    komisyon_orani = 0.001 # İşlem başına %0.1 borsa komisyonu (Standart Binance Oranı)

    for i in range(len(olasiliklar)):
        if olasiliklar[i] >= 0.65: # Belirlediğin %65 güven eşiği
            isleme_girilen_sayi += 1
            
            # 1. Alım İşlemi (Komisyon kasadan kesilir)
            islem_maliyeti = kasa * komisyon_orani
            kasa -= islem_maliyeti
            
            # 2. Mum kapanışındaki kar/zararı kasaya yansıt
            islem_getirisi = kasa * gercek_getiri_oranlari[i]
            kasa += islem_getirisi
            
            # 3. Satım İşlemi / Pozisyonu Kapatma (Çıkış komisyonu kesilir)
            kasa -= (kasa * komisyon_orani)

            # Win Rate (Başarı oranı) istatistiği için
            if gercek_getiri_oranlari[i] > 0:
                basarili_islem += 1

    # --- SONUÇLARIN YAZDIRILMASI ---
    print("="*60)
    print(f"📊 {sembol} - YAPAY ZEKA GERÇEKÇİ BACKTEST RAPORU")
    print("="*60)
    
    if isleme_girilen_sayi > 0:
        win_rate = (basarili_islem / isleme_girilen_sayi) * 100
        net_kar_orani = ((kasa - baslangic_kasa) / baslangic_kasa) * 100
        
        print(f"💰 Başlangıç Kasası: {baslangic_kasa:.2f} USDT")
        print(f"💰 Final Kasası:     {kasa:.2f} USDT")
        
        # Kar/Zarar durumunu belirginleştir
        if net_kar_orani > 0:
            print(f"📈 Net Kar/Zarar:    +%{net_kar_orani:.2f} (KÂR ETTİ)")
        else:
            print(f"📉 Net Kar/Zarar:    %{net_kar_orani:.2f} (ZARAR ETTİ)")
            
        print(f"\n🤖 'AL' Dediği İşlem: {isleme_girilen_sayi} defa")
        print(f"🎯 Kendi Başına Doğru Bilme Oranı (Win Rate): %{win_rate:.2f}")
    else:
        print("\n🤖 Bot bu test döneminde %65 güveni aşacak fırsat bulamadı.")
    print("\n")

test_edilecekler = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"]
for sembol in test_edilecekler:
    gercek_backtest(sembol)