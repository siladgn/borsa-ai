import sys
import os
sys.path.append(os.getcwd())

from modules.teknik import kapsamli_veri_cek
from modules.ai_model import yapay_zeka_analizi
from modules.rl_agent import get_rl_strategy

print("Veri cekiliyor...")
data = kapsamli_veri_cek("BTCUSDT", interval="15m", n_bars=200)
if data is not None:
    print("Veri cekildi. Sutunlar:", data.columns[-10:].tolist())
    print("Yapay Zeka Analizi basliyor...")
    karar, guven_skoru, yon = yapay_zeka_analizi(data, "BTCUSDT")
    print("AI Sonucu:", karar, guven_skoru, yon)
    
    son = data.iloc[-1]
    fiyat = son['close']
    atr = son.get('atr', fiyat * 0.03)
    volatilite = atr / fiyat if fiyat > 0 else 0
    imbalance = son.get('order_book_imbalance', 0.5)
    
    print("RL Strateji hesaplaniyor...")
    rl_strat = get_rl_strategy(guven_skoru, yon, volatilite, imbalance)
    print("RL Sonucu:", rl_strat)
else:
    print("Veri cekilemedi.")
