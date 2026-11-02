from modules.teknik import kapsamli_veri_cek

def mtf_skor_hesapla(sembol):
    """
    1m, 15m, 1h ve 4h periyotlarını analiz eder ve ağırlıklı bir skor döner.
    """
    periyot_map = {
        "1m": "1m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h"
    }
    
    periyot_agirliklari = {
        "1m": 0.1, "15m": 0.4, "1h": 0.3, "4h": 0.2
    }
    
    toplam_skor = 0.0
    detaylar = {}
    
    for label, tv_interval in periyot_map.items():
        agirlik = periyot_agirliklari[label]
        try:
            data = kapsamli_veri_cek(sembol, interval=tv_interval)
            if data is None or data.empty:
                continue
            
            last = data.iloc[-1]
            periyot_skoru = 0
            
            # 1. Trend (EMA 20/50/200)
            if last['close'] > last['ema200']: periyot_skoru += 30
            elif last['close'] < last['ema200']: periyot_skoru -= 30
            
            # 2. Momentum (RSI)
            if last['rsi'] > 55: periyot_skoru += 20
            elif last['rsi'] < 45: periyot_skoru -= 20
            
            # 3. SuperTrend
            if last['supertrend_sinyal'] == 1: periyot_skoru += 30
            elif last['supertrend_sinyal'] == -1: periyot_skoru -= 30
            
            # 4. Volatilite (BB Bandwidth / Squeeze)
            # Sıkışma varsa skoru artır (patlama potansiyeli)
            if last.get('squeeze_aktif', 0) == 1:
                periyot_skoru *= 1.2
            
            toplam_skor += periyot_skoru * agirlik
            detaylar[label] = periyot_skoru
            
        except Exception as e:
            print(f"Error in MTF ({label}): {e}")
            
    return round(toplam_skor, 2), detaylar

if __name__ == "__main__":
    skor, det = mtf_skor_hesapla("BTCUSDT")
    print(f"📊 MTF Toplam Skor: {skor}")
    print(f"🔍 Detaylar: {det}")
