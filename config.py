import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY") # CryptoPanic API (Haberler için)

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise EnvironmentError(
        ".env dosyası bulunamadı veya eksik.\n"
        ".env.example dosyasını kopyalayıp doldurun:\n"
        "  cp .env.example .env"
    )

# Risk ve Bakiye Ayarları (.env'den çekilir, yoksa varsayılan)
TOPLAM_BAKIYE = float(os.getenv("TOPLAM_BAKIYE", 100))
ISLEM_BASI_RISK = float(os.getenv("ISLEM_BASI_RISK", 0.02))

# Paper Trading (Sanal Para)
# .env dosyasında PAPER_TRADING=False yaparsanız GERÇEK İŞLEM başlar!
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"
PAPER_START_BALANCE = float(os.getenv("PAPER_START_BALANCE", 100))

# Kripto için yön değiştirme (flip): LONG->SHORT / SHORT->LONG
CRYPTO_FLIP_ON_SIGNAL = True

# Risk kontrolü
PAPER_MAX_OPEN_POSITIONS = int(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5))

# PAPER_TRADING=False iken tarama sinyaliyle Binance vadeli otomatik giriş (False: yalnızca dashboard niyeti vb.)
AUTO_BINANCE_TRADING = False

# Günde en az bu kadar işlem: kota dolmadan önce giriş filtreleri gevşer; hedef TP1 (küçük kâr da olsa yeter).
# Sabit USD tutarı değildir; 0 = bu kota/gevşetme kapalı.
MIN_DAILY_TRADES = int(os.getenv("MIN_DAILY_TRADES", "5"))

# Flip kalite filtreleri (kripto)
# guven_skoru = olasılık (0-100). LONG flip için min, SHORT flip için max.
CRYPTO_FLIP_LONG_MIN_SCORE = 60.0
CRYPTO_FLIP_SHORT_MAX_SCORE = 40.0

# Otomatik performans raporu (Telegram)
PAPER_SEND_DAILY_REPORT = True
PAPER_SEND_WEEKLY_REPORT = True

# ---------------------------
# Twitter (X) duyarlılık ekleri
# ---------------------------
# Not: Twitter'dan doğrudan veri çekmek zor/limitli olabildiği için RSS proxy (Nitter) kullanıyoruz.
# Kullanılan Nitter instance erişilemezse boş listeyle bırakabilir veya BASE'i değiştirebilirsin.
TWITTER_NITTER_BASE = "https://nitter.net"

# Önemli hesaplar (handle kısmı; başında @ yok)
TWITTER_IMPORTANT_ACCOUNTS = {
    # Not: Nitter RSS bazı hesaplarda erişim vermeyebilir; program devam eder.
    "BTCUSDT": [
        # Crypto / endüstri
        "saylor",
        "cz_binance",
        "brian_armstrong",
        "elonmusk",
        "gavinwood",  # BTC/ETH etkisi olabiliyor
        # Makro / finans
        "federalreserve",
        "ecb",
        "bankofengland",
        "imfnews",
        "worldbank",
        # Devlet/kurum liderliği (kurum hesapları)
        "potus",
        "whitehouse",
        "kremlinrussia",
        "zelenskyyua",
        "realDonaldTrump",
        # Büyük milyarderler (genel piyasa etkisi)
        "billgates",
        "jeffbezos",
        "warrenbuffett",
        "markzukerberg",
    ],
    "ETHUSDT": [
        # Crypto / endüstri
        "vitalikbuterin",
        "ethereum",
        "cz_binance",
        "brian_armstrong",
        "gavinwood",
        "elonmusk",
        "justinsun",
        # Makro / finans
        "federalreserve",
        "ecb",
        "bankofengland",
        "imfnews",
        "worldbank",
        # Devlet/kurum liderliği (kurum hesapları)
        "potus",
        "whitehouse",
        "kremlinrussia",
        "zelenskyyua",
        "realDonaldTrump",
        # Büyük milyarderler
        "billgates",
        "jeffbezos",
        "warrenbuffett",
        "markzukerberg",
    ],
}

# Her Twitter RSS kaynağından çekilecek max tweet sayısı
TWITTER_MAX_ENTRIES_PER_SOURCE = 12
