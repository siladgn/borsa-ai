import pandas as pd
import os

def sanal_sonuc_ozet(csv_path="data/sinyaller.csv"):
    if not os.path.exists(csv_path):
        print("🕵️ Henüz bir sinyal üretilmedi. Avcı botu (main.py) çalıştırıp bekleyin.")
        return

    df = pd.read_csv(csv_path, names=["tarih", "hisse", "fiyat", "rsi", "haber_skoru", "guven_skoru", "sonuc"])
    
    print(f"\n{'='*60}")
    print(f"📈 SANAL DENEME (PAPER TRADING) PERFORMANS TABLOSU")
    print(f"{'='*60}")
    print(f"Toplam İşlem Sayısı : {len(df)}")
    
    # Son 5 Sinyali ve Giriş Fiyatlarını Göster
    print("\nSON SİNYALLER VE GİRİŞ SEVİYELERİ:")
    print(df[['tarih', 'hisse', 'fiyat', 'guven_skoru']].tail(5).to_string(index=False))
    
    print(f"\n{'='*60}")
    print("💡 İPUCU: Bu fiyatlar üzerinden 'Stop' veya 'Hedef' gelip gelmediğini")
    print("TradingView üzerinden kontrol ederek sanal kârınızı hesaplayabilirsiniz.")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    sanal_sonuc_ozet()