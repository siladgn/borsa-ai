import feedparser
import time
import warnings
import sys
import os
import requests
from transformers import pipeline

# Ana dizini ekle
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    TWITTER_NITTER_BASE,
    TWITTER_IMPORTANT_ACCOUNTS,
    TWITTER_MAX_ENTRIES_PER_SOURCE,
    CRYPTOPANIC_API_KEY, # Eklendi
)

warnings.filterwarnings('ignore')

# Modeller lazy loading — ilk çağrıda yüklenir, sonra bellekte kalır
_finbert    = None
_turkce_nlp = None

def _model_yukle():
    global _finbert, _turkce_nlp
    if _finbert is None:
        _finbert = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            truncation=True, max_length=512
        )
    if _turkce_nlp is None:
        _turkce_nlp = pipeline(
            "sentiment-analysis",
            model="savasy/bert-base-turkish-sentiment-cased",
            truncation=True, max_length=512
        )

SEMBOL_ANAHTAR = {
    "THYAO.IS": ["THY", "Türk Hava", "Turkish Airlines", "THYAO"],
    "TUPRS.IS": ["Tüpraş", "TUPRS", "Tupras"],
    "ASELS.IS": ["Aselsan", "ASELS"],
    "SASA.IS":  ["SASA", "Sasa Polyester"],
    "KCHOL.IS": ["Koç Holding", "KCHOL"],
    "BTCUSDT":  ["Bitcoin", "BTC", "crypto"],
    "ETHUSDT":  ["Ethereum", "ETH"],
    "AAPL":     ["Apple", "AAPL", "iPhone", "Tim Cook"],
    "NVDA":     ["Nvidia", "NVDA", "GPU", "Jensen Huang"],
    "TSLA":     ["Tesla", "TSLA", "Elon Musk"],
}

def _kaynaklar(sembol):
    if sembol.endswith("USDT") or sembol in ("BTC", "ETH"):
        kaynaklar = [
            ("https://cointelegraph.com/rss", "EN"),
            ("https://coindesk.com/arc/outboundfeeds/rss/", "EN"),
            ("https://bitcoinmagazine.com/.rss/full/", "EN"), # Bitcoin Magazine
            ("https://www.newsbtc.com/feed/", "EN"), # NewsBTC
            ("https://cryptopotato.com/feed/", "EN"), # CryptoPotato
        ]
        # Twitter/X (RSS proxy) ekle (BTCUSDT/ETHUSDT için)
        key = sembol if sembol.endswith("USDT") else f"{sembol}USDT"
        handles = TWITTER_IMPORTANT_ACCOUNTS.get(key, [])
        for h in handles:
            kaynaklar.append((f"{TWITTER_NITTER_BASE}/{h}/rss", "EN"))
        return kaynaklar
    elif sembol.endswith(".IS"):
        # Çalışan, stabil ve güncel Türkçe ekonomi RSS kaynakları
        return [
            ("https://www.bloomberght.com/rss", "TR"),
            ("https://www.trthaber.com/ekonomi_articles.rss", "TR"),
            ("https://www.haberturk.com/rss/ekonomi.xml", "TR"),
            ("https://www.dunya.com/rss", "TR"), # Dünya Gazetesi
        ]
    else:
        return [
            (f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sembol}", "EN"),
            ("https://feeds.reuters.com/reuters/businessNews", "EN"),
        ]

def cryptopanic_haberleri_cek(sembol):
    """CryptoPanic API üzerinden haberleri çeker."""
    if not CRYPTOPANIC_API_KEY:
        return []
        
    s = sembol.replace("USDT", "")
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTOPANIC_API_KEY}&currencies={s}&kind=news&filter=hot"
    
    try:
        r = requests.get(url, timeout=10)
        if r.ok:
            data = r.json()
            results = []
            for post in data.get("results", []):
                results.append({
                    "title": post.get("title"),
                    "url": post.get("url"),
                    "sentiment": post.get("votes", {}).get("positive", 0) - post.get("votes", {}).get("negative", 0)
                })
            return results
    except Exception as e:
        print(f"⚠️ CryptoPanic Hatası: {e}")
    return []

def _ilgili_mi(baslik, sembol):
    anahtar = SEMBOL_ANAHTAR.get(sembol, [sembol.replace(".IS", "")])
    bl = baslik.lower()
    return any(k.lower() in bl for k in anahtar)

def _skor(baslik, dil):
    if dil == "EN":
        sonuc = _finbert(baslik)[0]
        label, conf = sonuc["label"], sonuc["score"]
        if label == "positive":  return  conf
        if label == "negative":  return -conf
        return 0.0
    else:
        sonuc = _turkce_nlp(baslik)[0]
        label, conf = sonuc["label"], sonuc["score"]
        if label in ("positive", "LABEL_1"):  return  conf
        if label in ("negative", "LABEL_0"):  return -conf
        return 0.0

def haber_duyarliligi(sembol, min_haber=3):
    _model_yukle()

    haberler = []  # [(baslik, zaman, dil)]

    # 1. Aşama: CryptoPanic API (Kripto ise)
    if sembol.endswith("USDT") or sembol in ("BTC", "ETH"):
        panic_data = cryptopanic_haberleri_cek(sembol)
        for p in panic_data:
            haberler.append((p['title'], "NOW", "EN"))

    # 2. Aşama: RSS Kaynaklarından Haber Arama
    for url, dil in _kaynaklar(sembol):
        try:
            feed = feedparser.parse(url)
            entry_limit = TWITTER_MAX_ENTRIES_PER_SOURCE if TWITTER_NITTER_BASE in url else 30
            for entry in feed.entries[:entry_limit]: # Daha derine iniyoruz
                baslik = entry.get("title", "").strip()
                if not baslik:
                    continue
                if _ilgili_mi(baslik, sembol):
                    zaman = entry.get("published_parsed") or time.gmtime()
                    if not any(b[0] == baslik for b in haberler): # Aynı haberi 2 kez ekleme
                        haberler.append((baslik, zaman, dil))
        except Exception as e:
            continue # Hata verirse program çökmesin, diğer kaynağa geçsin

    # 2. Aşama (Yedek Plan): Hisse haberi yoksa, Genel Piyasa Duyarlılığını al!
    if len(haberler) < min_haber:
        for url, dil in _kaynaklar(sembol):
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:2]: # Her kaynaktan en yeni 2 haberi al
                    baslik = entry.get("title", "").strip()
                    if baslik and not any(b[0] == baslik for b in haberler):
                        haberler.append((baslik, time.gmtime(), dil))
            except Exception:
                continue
            if len(haberler) >= min_haber:
                break

    if not haberler:
        return 0.0

    # Yeniye göre sırala
    haberler.sort(key=lambda x: x[1], reverse=True)

    skorlar, agirliklar = [], []
    for i, (baslik, _, dil) in enumerate(haberler[:5]): # API yorulmasın diye en iyi 5 haberi oku
        try:
            s = _skor(baslik, dil)
            # BURASI ÖNEMLİ: Ekranda AI'ın hangi habere ne puan verdiğini göreceksin!
            print(f"   📰 [HABER] {baslik[:65]}... -> AI Skoru: {s:.2f}")
            
            w = 1.0 / (i + 1)   # Yeni haber daha ağırlıklı
            skorlar.append(s * w)
            agirliklar.append(w)
        except Exception:
            continue

    if not agirliklar:
        return 0.0

    sonuc = sum(skorlar) / sum(agirliklar)
    return round(max(-1.0, min(1.0, sonuc)), 2)