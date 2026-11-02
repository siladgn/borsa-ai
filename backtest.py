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

    df["gelecek_getiri"] = df["close"].pct_change().shift(-1)
    df["target"] = (df["gelecek_getiri"] > 0).astype(int)
    df = df.dropna()

    from modules.ai_model import OZELLIKLER as ozellikler, model_egit
    bolme_noktasi = int(len(df) * 0.8)
    X_egitim = df[ozellikler].iloc[:bolme_noktasi].values
    y_egitim = df["target"].iloc[:bolme_noktasi].values
    X_test = df[ozellikler].iloc[bolme_noktasi:].values
    y_test_gercek = df["target"].iloc[bolme_noktasi:].values

    scaler = StandardScaler()
    X_egitim_scaled = scaler.fit_transform(X_egitim)
    X_test_scaled = scaler.transform(X_test)

    model = model_egit(X_egitim_scaled, y_egitim)
    olasiliklar = model.predict_proba(X_test_scaled)[:, 1]

    isleme_girilen_sayi = 0
    basarili_islem = 0

    for i in range(len(olasiliklar)):
        if olasiliklar[i] >= 0.65: # Kriptoda güven eşiğine göre test
            isleme_girilen_sayi += 1
            if y_test_gercek[i] == 1:
                basarili_islem += 1

    print("="*60)
    print(f"📊 {sembol} - YAPAY ZEKA GEÇMİŞ PERFORMANS TESTİ")
    print("="*60)
    if isleme_girilen_sayi > 0:
        win_rate = (basarili_islem / isleme_girilen_sayi) * 100
        print(f"🤖 'AL' Dediği İşlem Sayısı: {isleme_girilen_sayi} defa")
        print(f"🎯 Kendi Başına Doğru Bilme Oranı (Win Rate): %{win_rate:.2f}")
    else:
        print("\n🤖 Bot bu test döneminde %65 güveni aşacak fırsat bulamadı.")

test_edilecekler = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"]
for sembol in test_edilecekler:
    gercek_backtest(sembol)