import pandas as pd
import numpy as np

def likidite_bolgeleri_bul(data):
    """
    Fiyatın 'patlamaya' hazır olduğu veya likiditenin biriktiği seviyeleri bulur.
    Mantık: Order Blocks + POC + Yuvarlak Sayılar.
    """
    if data is None or data.empty:
        return []
        
    last = data.iloc[-1]
    fiyat = last['close']
    
    bolgeler = []
    
    # 1. Mevcut OB ve POC (Teknik modülden geliyor)
    if 'poc_mesafe' in data.columns:
        poc_price = fiyat / (1 + last['poc_mesafe'])
        bolgeler.append({"fiyat": poc_price, "tip": "POC", "guc": 0.8})
        
    # 2. Yuvarlak Sayı Likiditesi (Kriptoda çok kritiktir)
    # Örn: 60000, 65000, 70000
    round_step = 1000 if fiyat > 10000 else (100 if fiyat > 1000 else 10)
    round_up = ((fiyat // round_step) + 1) * round_step
    round_down = (fiyat // round_step) * round_step
    
    bolgeler.append({"fiyat": round_up, "tip": "PSIKOLOJIK_UST", "guc": 0.5})
    bolgeler.append({"fiyat": round_down, "tip": "PSIKOLOJIK_ALT", "guc": 0.5})
    
    # 3. Likidite Kümesi (Liquidation Clusters)
    # Son 100 mumda en çok iğne atılan ama geçilemeyen yerler
    recent = data.iloc[-100:]
    
    # HATA DÜZELTME: Küsuratlı float değerlerde value_counts() ile küme aramak mantıklı olmaz.
    # Fiyatın büyüklüğüne göre anlamlı bir basamağa yuvarlayarak en çok tekrar eden (likidite biriken) bölgeyi bulmalıyız.
    if fiyat < 1:
        rnd = 4
    elif fiyat < 100:
        rnd = 2
    elif fiyat < 1000:
        rnd = 1
    else:
        rnd = -1  # Onlar basamağına yuvarla (Örn: 64532 -> 64530)
        
    high_cluster = recent['high'].round(rnd).value_counts().idxmax()
    low_cluster = recent['low'].round(rnd).value_counts().idxmax()
    
    bolgeler.append({"fiyat": high_cluster, "tip": "LIKIDITE_HAVUZU_UST", "guc": 0.7})
    bolgeler.append({"fiyat": low_cluster, "tip": "LIKIDITE_HAVUZU_ALT", "guc": 0.7})
    
    return bolgeler

def volatilite_tahmini(data):
    """
    Piyasanın ne kadar 'gergin' olduğunu ölçer. 
    Düşük volatilite + Squeeze = Sert Patlama Yakındır.
    """
    if data is None or data.empty:
        return {"gerginlik": 0.0, "beklenen_hareket": "NORMAL", "mesaj": "Veri Yok"}
        
    recent = data.iloc[-20:]
    
    # HATA DÜZELTME: Eğer 'atr' sütunu yoksa kodun çökmemesi için eklendi ve hesaplanan atr_std kullanıma alındı.
    if 'atr' in recent.columns:
        atr_std = recent['atr'].std() / (recent['atr'].mean() + 1e-9)
    else:
        atr_std = 1.0
        
    # Squeeze analizi
    # HATA DÜZELTME: 'squeeze_aktif' sütununun olmama ihtimaline karşı .get() kullanılarak default 0 eklendi.
    sqz_count = data.get('squeeze_aktif', pd.Series(0, index=data.index)).iloc[-10:].sum()
    
    gerginlik_skoru = 0.0
    if sqz_count >= 8: # Son 10 mumun 8'i squeeze
        gerginlik_skoru = 0.9 # Patlama çok yakın
    elif sqz_count >= 5:
        gerginlik_skoru = 0.6
    elif atr_std < 0.1: # ATR çok stabil ise (std dev, mean'in %10'undan küçükse), bu da bir gerginlik ve sıkışma belirtisidir
        gerginlik_skoru = 0.5
        
    return {
        "gerginlik": round(gerginlik_skoru, 2),
        "beklenen_hareket": "YUKSEK" if gerginlik_skoru > 0.7 else "NORMAL",
        "mesaj": "⚠️ Fırtına Öncesi Sessizlik!" if gerginlik_skoru > 0.7 else "Piyasa sakin."
    }

if __name__ == "__main__":
    # Mock data test
    print("💧 Likidite Analizi Testi...")
