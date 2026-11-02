
import numpy as np


def risk_odul_hesapla(entry, stop, tp, komisyon=0.0004):
    """
    Komisyon dahil Risk/Ödül oranı hesaplar.
    entry: Giriş fiyatı
    stop: Stop Loss fiyatı
    tp: Take Profit fiyatı
    komisyon: Tek yön komisyon oranı (varsayılan %0.04)
    """
    komisyon_toplam = entry * komisyon * 2  # Giriş + Çıkış komisyonu
    risk = abs(entry - stop) + komisyon_toplam
    odul = abs(tp - entry) - komisyon_toplam
    if risk <= 0 or odul <= 0:
        return 0.0
    return round(odul / risk, 2)


def kelly_risk_hesapla(guven_skoru, win_rate=0.55, risk_reward_ratio=2.0):
    """
    Kelly Criterion kullanarak dinamik risk oranı hesaplar.
    Formül: K% = (p * b - q) / b
    p: Kazanma olasılığı (AI Güven Skoru baz alınır)
    b: Risk/Reward oranı (Hedef Kar / Stop Mesafesi)
    q: Kaybetme olasılığı (1 - p)
    """
    # AI Güven Skorunu (0-100) olasılığa (0-1) çevir
    # Güven skoru 50 ise p=0.5, 80 ise p=0.8
    p = guven_skoru / 100.0
    
    # Kazanma olasılığını botun geçmiş win_rate'i ile harmanla (Daha güvenli)
    p = (p + win_rate) / 2.0
    
    q = 1.0 - p
    b = risk_reward_ratio
    
    if b <= 0: return 0.01 # Hatalı oran gelirse min %1
    
    kelly_f = (p * b - q) / b
    
    # Kelly bazen çok agresif olabilir, bu yüzden "Half-Kelly" veya "Quarter-Kelly" kullanılır.
    # Güvenliği artırmak için Eighth-Kelly (%12.5) seviyesine çekiyoruz.
    safe_kelly = kelly_f * 0.125
    
    # Sınırlandırmalar: Max %3, Min %0.3 risk
    final_risk = max(0.003, min(0.03, safe_kelly))
    
    return final_risk

def balina_duvarlari_bul(sembol, depth=50):
    """
    Binance Order Book (Emir Defteri) üzerinden büyük alış/satış duvarlarını bulur.
    """
    import requests
    try:
        url = f"https://api.binance.com/api/v3/depth?symbol={sembol}&limit={depth}"
        r = requests.get(url, timeout=5)
        if not r.ok: return None
        
        data = r.json()
        bids = data['bids'] # Alışlar [[fiyat, miktar], ...]
        asks = data['asks'] # Satışlar
        
        # Miktarları float'a çevir
        bids = [[float(p), float(q)] for p, q in bids]
        asks = [[float(p), float(q)] for p, q in asks]
        
        # Ortalama emir büyüklüğünü bul
        avg_bid = np.mean([q for p, q in bids])
        avg_ask = np.mean([q for p, q in asks])
        
        # Ortalamanın 5 katından büyük olanları "Duvar" kabul et
        bid_walls = [p for p, q in bids if q > avg_bid * 5]
        ask_walls = [p for p, q in asks if q > avg_ask * 5]
        
        return {
            "destek_duvarlari": bid_walls[:3], # En yakın 3 büyük alış
            "direnc_duvarlari": ask_walls[:3], # En yakın 3 büyük satış
            "duvar_gucu": (avg_bid / avg_ask) if avg_ask > 0 else 1.0 # Alış baskısı / Satış baskısı
        }
    except Exception as e:
        print(f"Balina duvarı hatası ({sembol}): {e}")
        return None

def order_book_imbalance_hesapla(sembol, depth=20):
    """
    Binance Order Book üzerinden Emir Defteri Dengesizliği (Imbalance) 
    ve potansiyel Spoofing metriklerini hesaplar.
    """
    import requests
    try:
        url = f"https://api.binance.com/api/v3/depth?symbol={sembol}&limit={depth}"
        r = requests.get(url, timeout=5)
        if not r.ok: 
            return {"imbalance": 0.5, "spoofing_metric": 0.0}
        
        data = r.json()
        bids = [[float(p), float(q)] for p, q in data['bids']]
        asks = [[float(p), float(q)] for p, q in data['asks']]
        
        total_bid_vol = sum(q for p, q in bids)
        total_ask_vol = sum(q for p, q in asks)
        
        if (total_bid_vol + total_ask_vol) == 0:
            return {"imbalance": 0.5, "spoofing_metric": 0.0}
            
        # Imbalance Ratio: (Bid Vol - Ask Vol) / (Bid Vol + Ask Vol)
        imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)
        
        # Spoofing metriği: Uzaktaki 10 emrin hacmi / Yakındaki 10 emrin hacmi
        far_bid_vol = sum(q for p, q in bids[10:]) if len(bids) > 10 else 0
        near_bid_vol = sum(q for p, q in bids[:10]) if len(bids) > 10 else total_bid_vol
        far_ask_vol = sum(q for p, q in asks[10:]) if len(asks) > 10 else 0
        near_ask_vol = sum(q for p, q in asks[:10]) if len(asks) > 10 else total_ask_vol
        
        bid_spoof_ratio = far_bid_vol / (near_bid_vol + 1e-8)
        ask_spoof_ratio = far_ask_vol / (near_ask_vol + 1e-8)
        
        spoofing_metric = bid_spoof_ratio - ask_spoof_ratio
        
        # Normalize edilmiş imbalance (0 ile 1 arasına çek: 0=max satıcı, 1=max alıcı)
        norm_imbalance = (imbalance + 1.0) / 2.0
        
        return {
            "imbalance": norm_imbalance,
            "spoofing_metric": spoofing_metric
        }
    except Exception as e:
        print(f"Imbalance hesabı hatası ({sembol}): {e}")
        return {"imbalance": 0.5, "spoofing_metric": 0.0}
