import streamlit as st
import pandas as pd
import sqlite3
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import os
import json
import requests
import numpy as np
import feedparser
import time
from typing import Optional

# Opsiyonel: teknik veri + AI (dashboard çalışma dizininden)
try:
    from modules.teknik import kapsamli_veri_cek
    from modules.ai_model import yapay_zeka_analizi
    from modules import paper_trader, database
except Exception:
    kapsamli_veri_cek = None
    yapay_zeka_analizi = None
    paper_trader = None
    database = None

try:
    from config import PAPER_TRADING, PAPER_START_BALANCE
    DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "borsaai123")
except Exception:
    PAPER_TRADING = True
    PAPER_START_BALANCE = 100.0
    DASHBOARD_PASSWORD = "borsaai123"

# --- Giriş Kontrolü ---
def check_password():
    """Kullanıcı şifresini kontrol eder."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("<h1 style='text-align: center;'>🔐 BorsaAI Giriş</h1>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            password = st.text_input("Sistem Şifresi", type="password")
            if st.button("Giriş Yap"):
                if password == DASHBOARD_PASSWORD:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("❌ Hatalı şifre!")
        return False
    return True

if not check_password():
    st.stop()

# --- Sabitler ---
DB_PATH = "data/borsa_ai.db"
PAPER_STATE_PATH = "data/paper_state.json"
TRADE_INTENT_PATH = "data/ui_trade_intent.json"
STABLE_BASES = {
    "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "GBP",
    "USDD", "UST", "USDE", "AEUR", "EURI",
}

ANALIZ_TIPLERI = {
    "genel": "Genel teknik analiz",
    "trend": "Trend ve momentum",
    "hacim": "Hacim ve fiyat aksiyonu",
    "scalp": "Kısa vadeli scalping",
    "vol_kirilim": "Volatilite ve kırılım",
    "balina": "Balina analizleri",
    "orderflow": "Order flow ve smart money",
}

RISK_PROFILE_HELP = {
    "normal": "Dengeli giriş eşikleri ve orta kaldıraç üst sınırı.",
    "safe": "Temkinli: daha geniş SL, düşük kaldıraç ve dar pozisyon boyutu.",
    "agresif": "Agresif: daha sıkı SL, yüksek kaldıraç üst sınırı (yüksek risk).",
}


@st.cache_data(ttl=300)
def get_trending_usdt_market():
    """Binance 24s ticker — 5 dk önbellek. Yükselenler, düşenler, USDT hacim liderleri."""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
        if not r.ok:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        rows = r.json()
        recs = []
        for d in rows:
            sym = d.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym.replace("USDT", "")
            if base in STABLE_BASES:
                continue
            try:
                recs.append({
                    "symbol": sym,
                    "base": base,
                    "change_pct": float(d["priceChangePercent"]),
                    "last": float(d["lastPrice"]),
                    "quote_vol": float(d["quoteVolume"]),
                    "count": int(d["count"]),
                })
            except (TypeError, ValueError, KeyError):
                continue
        if not recs:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        df = pd.DataFrame(recs)
        gainers = df.nlargest(10, "change_pct").reset_index(drop=True)
        losers = df.nsmallest(10, "change_pct").reset_index(drop=True)
        vol_leaders = df.nlargest(10, "quote_vol").reset_index(drop=True)
        return gainers, losers, vol_leaders
    except Exception:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


@st.cache_data(ttl=300)
def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()["data"][0]
        return int(d["value"]), str(d["value_classification"])
    except Exception:
        return None, "—"


@st.cache_data(ttl=300)
def get_global_market_cap():
    """CoinGecko global: toplam piyasa değeri ve 24s değişim."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        if not r.ok:
            return None, None, None
        g = r.json().get("data", {})
        tmc = g.get("total_market_cap", {}).get("usd")
        ch = g.get("market_cap_change_percentage_24h_usd")
        dom = g.get("market_cap_percentage", {}).get("btc")
        return float(tmc) if tmc else None, float(ch) if ch is not None else None, float(dom) if dom else None
    except Exception:
        return None, None, None


def binance_last_price(symbol: str) -> Optional[float]:
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
            timeout=5,
        )
        if r.ok:
            return float(r.json()["price"])
    except Exception:
        pass
    return None


def etki_puani_baslik(baslik: str) -> tuple[int, list[str]]:
    """0–10 etki puanı; 7+ piyasayı güçlü etkileyebilecek olaylar varsayımı."""
    b = baslik.lower()
    nedenler = []
    puan = 4
    yuksek = [
        ("sec ", 2, "SEC"),
        ("etf", 2, "ETF"),
        ("hack", 3, "Güvenlik olayı"),
        ("exploit", 3, "Exploit"),
        ("ban", 2, "Regülasyon"),
        ("lawsuit", 2, "Dava"),
        ("approval", 2, "Onay"),
        ("binance", 1, "Borsa"),
        ("interest rate", 2, "Faiz"),
        ("cpi ", 2, "Enflasyon"),
        ("fomc", 2, "FOMC"),
        ("crash", 2, "Volatilite"),
        ("surge", 1, "Hareket"),
        ("whale", 1, "Balina"),
        ("liquidat", 2, "Likidite"),
    ]
    for anahtar, ek, etiket in yuksek:
        if anahtar in b:
            puan += ek
            if etiket not in nedenler:
                nedenler.append(etiket)
    if len(baslik) > 90:
        puan += 1
    puan = int(max(0, min(10, puan)))
    return puan, nedenler


def haberleri_lite_cek(limit: int = 12):
    """Ağır NLP olmadan RSS başlıkları + etki puanı + olası pariteler."""
    urls = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]
    cikti = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:8]:
                t = (e.get("title") or "").strip()
                if not t:
                    continue
                skor, nd = etki_puani_baslik(t)
                pariteler = []
                tl = t.lower()
                if "bitcoin" in tl or "btc" in tl:
                    pariteler.append("BTCUSDT")
                if "ethereum" in tl or " eth" in tl or tl.startswith("eth "):
                    pariteler.append("ETHUSDT")
                if "solana" in tl or "sol" in tl:
                    pariteler.append("SOLUSDT")
                if not pariteler:
                    pariteler.append("GENEL")
                cikti.append(
                    {
                        "baslik": t[:200],
                        "etki": skor,
                        "neden": ", ".join(nd) if nd else "—",
                        "parite": ", ".join(sorted(set(pariteler))),
                    }
                )
        except Exception:
            continue
        if len(cikti) >= limit:
            break
    return cikti[:limit]


def risk_profile_params(profile: str) -> dict:
    if profile == "safe":
        return {"lev_max": 5, "pct_max": 0.20, "sl_atr": 1.35, "tp_atr": 2.0}
    if profile == "agresif":
        return {"lev_max": 20, "pct_max": 0.45, "sl_atr": 0.85, "tp_atr": 2.8}
    return {"lev_max": 12, "pct_max": 0.30, "sl_atr": 1.1, "tp_atr": 2.2}


def yapilandir_tp_sl(data: pd.DataFrame, yon: str, rp: dict):
    """Son swing + ATR ile SL/TP; yapı: önceki dip/tepe yakını."""
    if data is None or data.empty or len(data) < 30:
        return None, None, None, None, "Veri yetersiz"
    last = data.iloc[-1]
    close = float(last["close"])
    atr = float(last.get("atr", close * 0.01) or close * 0.01)
    win = min(40, len(data) - 1)
    tail = data.tail(win)
    swing_low = float(tail["low"].min())
    swing_high = float(tail["high"].max())
    sl_a = rp["sl_atr"]
    tp_a = rp["tp_atr"]
    if yon == "LONG":
        sl_sw = swing_low * 0.997
        sl_atr = close - sl_a * atr
        sl = max(sl_sw, sl_atr)
        tp1 = close + 1.0 * atr
        tp2 = min(close + tp_a * atr, swing_high * 1.02 if swing_high > close else close + tp_a * atr)
        return sl, tp1, tp2, close, None
    if yon == "SHORT":
        sl_sw = swing_high * 1.003
        sl_atr = close + sl_a * atr
        sl = min(sl_sw, sl_atr)
        tp1 = close - 1.0 * atr
        tp2 = max(close - tp_a * atr, swing_low * 0.98 if swing_low < close else close - tp_a * atr)
        return sl, tp1, tp2, close, None
    return None, None, None, close, "Yön BEKLE/EXIT — otomatik giriş önerilmez"


def analiz_narratif(tip: str, data: pd.DataFrame, karar: str, guven: float, yon: str) -> str:
    """Analiz tipine göre kısa metin (AI motoru + göstergeler özet)."""
    if data is None or data.empty:
        return "Veri yok."
    row = data.iloc[-1]
    rsi = float(row.get("rsi", 50))
    adx = float(row.get("adx", 20))
    vr = float(row.get("volume_ratio", 1.0))
    parts = [f"**{ANALIZ_TIPLERI.get(tip, tip)}** | AI: *{karar}* (%{guven:.0f} güven, {yon})."]
    if tip == "genel":
        parts.append(
            f"RSI {rsi:.1f}, ADX {adx:.1f} — trend gücü ve aşırı alım/satım bölgesi birlikte okunmalı."
        )
    elif tip == "trend":
        parts.append(
            f"ADX {adx:.1f} ile trend filtresi; EMA/Supertrend hizası son mumlarda yapısal yönü destekliyor."
        )
    elif tip == "hacim":
        parts.append(f"Hacim oranı (vol/ort) ~{vr:.2f} — fiyat-hacim uyumu ve OBV eğimi kritik.")
    elif tip == "scalp":
        parts.append("Kısa periyot: dar bant sıkışması ve son 3–5 mumun kapanış kalitesi öne çıkar.")
    elif tip == "vol_kirilim":
        squeeze = int(row.get("bb_squeeze", 0) or 0)
        parts.append(
            "Bollinger sıkışması / genişleme ve ATR ile kırılım ihtimali değerlendirildi."
            + (" Sıkışma sinyali var." if squeeze else "")
        )
    elif tip == "balina":
        b = float(row.get("balina_baskisi", 0) or 0)
        parts.append(f"Emir defteri / balina baskısı skoru: {b:.3f} (büyük duvarlara dikkat).")
    elif tip == "orderflow":
        ob = int(row.get("ob_tip", 0) or 0)
        parts.append(
            f"Order block tipi {ob:+d}, VWAP sapması — kurumsal akışa yakın seviyeler izlenmeli."
        )
    parts.append(
        "Dominans, toplam grafik ve osilatör taraması bot döngüsünde sürekli; burada seçilen parite anlık özetlenir."
    )
    return "\n\n".join(parts)


def pozisyon_tavsiyesi_metni(sembol: str, pos: dict, last: Optional[float]) -> str:
    """Canlı durum değerlendirmesi (kural tabanlı asistan)."""
    side = pos.get("side", "?")
    entry = float(pos.get("entry", 0))
    tp1 = float(pos.get("tp1", 0))
    tp2 = float(pos.get("tp2", 0))
    stp = float(pos.get("stop", 0))
    if not last or entry <= 0:
        return "Anlık fiyat alınamadı; borsa bağlantısını kontrol edin."
    if side == "LONG":
        pnl_pct = (last - entry) / entry * 100
        dist_tp1 = (tp1 - last) / last * 100
        dist_sl = (last - stp) / last * 100
    else:
        pnl_pct = (entry - last) / entry * 100
        dist_tp1 = (last - tp1) / last * 100
        dist_sl = (stp - last) / last * 100

    satirlar = [f"**{sembol}** {side} | Giriş `{entry:.4f}` | Güncel `{last:.4f}` | PnL ~**%{pnl_pct:+.2f}**"]

    if pnl_pct > 1.2 and dist_tp1 > 0.4:
        satirlar.append("Kar koruma: TP1 yakın; **%30 kısmi kar** veya TP1 limiti düşünülebilir.")
    elif pnl_pct < -0.8:
        satirlar.append("Zarar bölgesi: yapı bozulmadıysa SL mesafesine dikkat; agresif ise **trailing stop** sıkılaştırılabilir.")
    else:
        satirlar.append("Bölge nötr: **Pozisyonu tut**; yapı kırılımı veya hacim patlaması beklenmeli.")

    if dist_sl < 0.35:
        satirlar.append("⚠️ Stop çok yakın — likidite avı / gürültü riski; SL öncesi tepe/dip yapısını gözden geçirin.")

    if dist_tp1 < 0.15:
        satirlar.append("TP1 bölgesindesiniz: **direnç kırılımı için ek momentum** (hacim + kapanış) teyidi faydalı olur.")

    if side == "LONG" and last > entry * 1.008:
        satirlar.append("Kârda ilerleme: **trailing stop** ile önceki swing low altına kademeli taşıma önerilir.")

    if side == "SHORT" and last < entry * 0.992:
        satirlar.append("SHORT lehine: kârı kilitlemek için **trailing stop** (son swing high üstü) düşünülebilir.")

    return "\n\n".join(satirlar)


# Sayfa Ayarları
st.set_page_config(
    page_title="BorsaAI PRO - Elite Terminal",
    layout="wide",
    page_icon="⚡",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@400;700&display=swap');
    :root {
        --primary-accent: #00ffcc;
        --secondary-accent: #ff00ff;
        --bg-dark: #0e1117;
        --card-bg: rgba(255, 255, 255, 0.03);
    }
    .stApp {
        background-color: var(--bg-dark);
        color: #e0e0e0;
        font-family: 'Inter', sans-serif;
    }
    .stMetric {
        background: var(--card-bg);
        padding: 20px;
        border-radius: 12px;
        border: 1px solid rgba(0, 255, 204, 0.1);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
    }
    h1, h2, h3 {
        font-family: 'Inter', sans-serif;
        font-weight: 800;
        letter-spacing: -1px;
        color: #ffffff !important;
    }
    .pro-title {
        background: linear-gradient(90deg, #00ffcc, #ff00ff);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 3.2rem !important;
        text-align: center;
        margin-bottom: 0;
    }
    .trending-strip {
        background: linear-gradient(180deg, rgba(0,255,204,0.06), transparent);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 14px;
        padding: 12px 16px;
        margin-bottom: 12px;
    }
    .trending-meta { color: #666; font-size: 0.78rem; margin-top: 4px; }
    .stTabs [data-baseweb="tab-list"] { gap: 15px; background-color: transparent; }
    .stTabs [data-baseweb="tab"] {
        height: 45px;
        background-color: var(--card-bg);
        border-radius: 8px;
        padding: 10px 25px;
        color: #888;
    }
    .stTabs [aria-selected="true"] {
        background-color: rgba(0, 255, 204, 0.1) !important;
        border: 1px solid var(--primary-accent) !important;
        color: var(--primary-accent) !important;
    }
    div[data-testid="stExpander"] {
        background-color: var(--card-bg);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=5)
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            islemler = pd.read_sql("SELECT * FROM islemler ORDER BY id DESC", conn)
            if "giris_zamani" in islemler.columns:
                islemler["giris_zamani"] = pd.to_datetime(
                    islemler["giris_zamani"], errors="coerce"
                )
            if "cikis_zamani" in islemler.columns:
                islemler["cikis_zamani"] = pd.to_datetime(
                    islemler["cikis_zamani"], errors="coerce"
                )
            tahminler = pd.read_sql("SELECT * FROM tahminler ORDER BY id DESC", conn)
            loglar = pd.read_sql("SELECT * FROM loglar ORDER BY id DESC LIMIT 200", conn)
        return islemler, tahminler, loglar
    except Exception as e:
        st.error(f"Veri yükleme hatası: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


def load_paper_state():
    if os.path.exists(PAPER_STATE_PATH):
        with open(PAPER_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


islemler, tahminler, loglar = load_data()
paper_state = load_paper_state()

if "risk_profile" not in st.session_state:
    st.session_state.risk_profile = "normal"
if "analiz_tipi" not in st.session_state:
    st.session_state.analiz_tipi = "genel"
if "trade_symbol" not in st.session_state:
    st.session_state.trade_symbol = "BTCUSDT"
if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = None


@st.cache_data(ttl=60)
def get_market_heatmap():
    symbols = [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "AVAXUSDT",
        "ADAUSDT",
        "LINKUSDT",
        "DOTUSDT",
        "MATICUSDT",
    ]
    heatmap_data = []
    for s in symbols:
        try:
            r = requests.get(
                f"https://api.binance.com/api/v3/ticker/24hr?symbol={s}",
                timeout=2,
            )
            if r.ok:
                d = r.json()
                heatmap_data.append(
                    {
                        "symbol": s.replace("USDT", ""),
                        "change": float(d["priceChangePercent"]),
                        "price": float(d["lastPrice"]),
                        "volume": float(d["quoteVolume"]),
                    }
                )
        except Exception:
            continue
    return pd.DataFrame(heatmap_data)

# Kenar Çubuğu
with st.sidebar:
    st.markdown("<h2 style='text-align:center'>⚡ BorsaAI</h2>", unsafe_allow_html=True)
    st.markdown(
        "<div style='text-align:center'><span style='padding:4px 12px;border-radius:20px;border:1px solid #00ffcc;color:#00ffcc;font-size:0.8rem'>System Live</span></div>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    balance = paper_state.get("balance", 0.0)
    st.markdown("### 💰 Terminal Bakiye")
    st.markdown(
        f"<h2 style='color:#00ffcc'>${balance:,.2f}</h2>",
        unsafe_allow_html=True,
    )
    daily_pnl = paper_state.get("daily_pnl", 0.0)
    pnl_color = "#00ffcc" if daily_pnl >= 0 else "#ff4b4b"
    st.markdown(
        f"**Günlük PnL:** <span style='color:{pnl_color}'>${daily_pnl:+.2f}</span>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("### ⚖️ Risk profili")
    rp_choice = st.radio(
        "Profil",
        options=["normal", "safe", "agresif"],
        format_func=lambda x: {
            "normal": "Normal",
            "safe": "Temkinli (Safe)",
            "agresif": "Agresif",
        }[x],
        index=["normal", "safe", "agresif"].index(st.session_state.risk_profile)
        if st.session_state.risk_profile in ("normal", "safe", "agresif")
        else 0,
        key="risk_radio",
    )
    st.session_state.risk_profile = rp_choice
    st.caption(RISK_PROFILE_HELP.get(rp_choice, ""))

    st.markdown("---")
    st.markdown("### ⚙️ Otomatik işlem ayarları")
    rp = risk_profile_params(rp_choice)
    lev = st.slider(
        "Kaldıraç (üst sınır profil ile sınırlı)",
        1,
        rp["lev_max"],
        min(5, rp["lev_max"]),
        help="Gerçek borsada yalnızca hesap izinleri dahilinde.",
    )
    bal_pct = st.slider(
        "Vadeli kasa kullanımı (%)",
        5,
        int(rp["pct_max"] * 100),
        min(20, int(rp["pct_max"] * 100)),
        help="Açılacak pozisyon için ayrılan bakiye yüzdesi (izole marjin).",
    ) / 100.0

    st.info(
        "**Marjin modu:** İzole (Isolated) — Cross kullanılmaz; tüm bakiyenin likidite riski tek pozisyonda sınırlı kalır."
    )
    st.caption("Take Profit / Stop Loss, analiz ekranında yapıya göre hesaplanır ve emir özetinde gösterilir.")

    st.markdown("---")
    st.markdown("### 🛰️ Network")
    st.caption(f"Senkron: {datetime.now().strftime('%H:%M:%S')}")
    if st.button("🚀 FORCE REFRESH", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### 🏥 System Health")
    is_bot_running = os.path.exists("data/bot.pid")
    st.markdown(
        f"**Bot:** {'Online' if is_bot_running else 'Offline'} | **DB:** Connected",
        unsafe_allow_html=True,
    )

# --- Ana üst başlık ---
st.markdown(
    "<h1 class='pro-title'>BORSA AI PRO TERMINAL</h1>", unsafe_allow_html=True
)
st.markdown(
    "<p style='text-align:center;color:#888;margin-top:-8px'>AI karar, risk yönetimi ve 7/24 tarama — tek terminal</p>",
    unsafe_allow_html=True,
)

# ========== 1) Trending Coins (5 dk) ==========
st.markdown('<div class="trending-strip">', unsafe_allow_html=True)
st.markdown("### 🔥 Trending Coins")
st.markdown(
    '<p class="trending-meta">Binance USDT pariteleri — liste <strong>5 dakikada</strong> bir yenilenir. '
    "Hacim sütunu: 24 saatlik USDT hacmi (en yüksek işlem akışı).</p>",
    unsafe_allow_html=True,
)
gainers, losers, vol_df = get_trending_usdt_market()
tg1, tg2, tg3 = st.columns(3)
with tg1:
    st.markdown("##### 📈 En çok yükselenler")
    if not gainers.empty:
        st.dataframe(
            gainers[["base", "change_pct", "last", "quote_vol"]].rename(
                columns={
                    "base": "Coin",
                    "change_pct": "%24s",
                    "last": "Fiyat",
                    "quote_vol": "Hacim USDT",
                }
            ),
            hide_index=True,
            height=280,
            column_config={
                "Hacim USDT": st.column_config.NumberColumn(format="%.0f"),
                "%24s": st.column_config.NumberColumn(format="%.2f"),
            },
        )
    else:
        st.warning("Veri alınamadı.")
with tg2:
    st.markdown("##### 📉 En çok düşenler")
    if not losers.empty:
        st.dataframe(
            losers[["base", "change_pct", "last", "quote_vol"]].rename(
                columns={
                    "base": "Coin",
                    "change_pct": "%24s",
                    "last": "Fiyat",
                    "quote_vol": "Hacim USDT",
                }
            ),
            hide_index=True,
            height=280,
        )
    else:
        st.warning("Veri alınamadı.")
with tg3:
    st.markdown("##### 📊 En yüksek USDT hacim")
    st.caption("24s işlem hacmi — “hacim şoku” için bot taraması kullanılır.")
    if not vol_df.empty:
        st.dataframe(
            vol_df[["base", "change_pct", "quote_vol", "last"]].rename(
                columns={
                    "base": "Coin",
                    "change_pct": "%24s",
                    "quote_vol": "Hacim USDT",
                    "last": "Fiyat",
                }
            ),
            hide_index=True,
            height=280,
        )
    else:
        st.warning("Veri alınamadı.")
st.markdown("</div>", unsafe_allow_html=True)

# Makro şerit
fng_v, fng_l = get_fear_greed()
mcap, mcap_ch, btc_dom = get_global_market_cap()
m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Fear & Greed",
    f"{fng_v}" if fng_v is not None else "—",
    delta=fng_l if fng_l else None,
)
if mcap:
    m2.metric(
        "Global Market Cap (USD)",
        f"${mcap/1e12:.3f}T",
        delta=f"{mcap_ch:+.2f}% (24s)" if mcap_ch is not None else None,
    )
else:
    m2.metric("Global Market Cap", "—")
m3.metric("BTC Dominans", f"{btc_dom:.1f}%" if btc_dom else "—")
m4.metric(
    "Risk profili",
    {"normal": "Normal", "safe": "Temkinli", "agresif": "Agresif"}[
        st.session_state.risk_profile
    ],
)

# Heatmap
with st.container():
    try:
        heatmap_df = get_market_heatmap()
        if not heatmap_df.empty:
            fig_heat = px.treemap(
                heatmap_df,
                path=["symbol"],
                values="volume",
                color="change",
                color_continuous_scale=["#ff4b4b", "#333333", "#00ffcc"],
                color_continuous_midpoint=0,
                hover_data=["price", "change"],
                title="Market Heatmap (24s değişim & hacim)",
            )
            fig_heat.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=40, b=0),
                height=280,
            )
            st.plotly_chart(fig_heat, width="stretch")
    except Exception as e:
        st.error(f"Heatmap: {e}")

# Üst metrikler (işlemler)
m1, m2, m3, m4 = st.columns(4)
daily_pnl = paper_state.get("daily_pnl", 0.0)
if not islemler.empty:
    acik_islem_sayisi = len(islemler[islemler["durum"] == "ACIK"])
    toplam_islem = len(islemler)
    kapali_islemler = islemler[islemler["durum"] == "KAPALI"]
    basarili_islem = len(kapali_islemler[kapali_islemler["kar_zarar"] > 0])
    win_rate = (
        (basarili_islem / len(kapali_islemler)) * 100 if len(kapali_islemler) > 0 else 0
    )
    total_net_pnl = islemler["kar_zarar"].sum()
    m1.metric("Açık pozisyon", f"{acik_islem_sayisi}")
    m2.metric("Toplam işlem", toplam_islem)
    m3.metric("Kazanma oranı", f"%{win_rate:.1f}")
    m4.metric("Net PnL", f"${total_net_pnl:.2f}", delta=f"{daily_pnl:+.2f} bugün")
else:
    m1.metric("Açık pozisyon", "0")
    m2.metric("Toplam işlem", "0")
    m3.metric("Kazanma oranı", "%0")
    m4.metric("Net PnL", "$0.00")

# --- AI Pozisyon Tavsiyesi ---
st.markdown("### 🤖 AI Pozisyon Tavsiyesi")
aciklar = paper_state.get("positions", {})
if aciklar:
    for sembol, pos in aciklar.items():
        if pos.get("status") not in ("OPEN", "TP1"):
            continue
        px_ = binance_last_price(sembol)
        with st.expander(f"{sembol} — {pos.get('side')} @ {pos.get('entry', 0):.4f}", expanded=True):
            st.markdown(pozisyon_tavsiyesi_metni(sembol, pos, px_))
else:
    st.info("Açık pozisyon yok; tavsiye modülü pozisyon açıldığında canlı güncellenir.")

# --- Komuta merkezi: analiz tipleri + işleme gir ---
st.markdown("### 🎯 AI Komuta Merkezi")
st.caption(
    "Dominans, hacim, mum yapısı, osilatör ve indikatörler motor tarafında taranır; burada parite ve analiz tipi seçerek anlık özet ve TP/SL üretirsiniz."
)
cc1, cc2 = st.columns([1, 1])
with cc1:
    sembol_in = st.text_input(
        "Parite (örn. BTCUSDT)",
        value=st.session_state.trade_symbol,
        key="sym_in",
    ).upper().strip()
    if sembol_in:
        st.session_state.trade_symbol = sembol_in
    analiz_tipi = st.selectbox(
        "Analiz tipi",
        options=list(ANALIZ_TIPLERI.keys()),
        format_func=lambda k: ANALIZ_TIPLERI[k],
        index=list(ANALIZ_TIPLERI.keys()).index(st.session_state.analiz_tipi)
        if st.session_state.analiz_tipi in ANALIZ_TIPLERI
        else 0,
    )
    st.session_state.analiz_tipi = analiz_tipi
    run = st.button("⚡ Analizi çalıştır (saniyeler içinde)", type="primary")

with cc2:
    st.markdown("##### Otomatik risk")
    st.write(
        f"Profil: **{st.session_state.risk_profile}** | Kaldıraç: **{lev}x** | Kasa: **%{bal_pct*100:.0f}** | Marjin: **İzole**"
    )

analiz_data = None
karar, guven, yon = "—", 0.0, "EXIT"
if run and kapsamli_veri_cek and yapay_zeka_analizi:
    with st.spinner("Piyasa verisi ve AI skorları hesaplanıyor..."):
        try:
            analiz_data = kapsamli_veri_cek(sembol_in, interval="15m", n_bars=800)
            if analiz_data is not None and not analiz_data.empty:
                karar, guven, yon = yapay_zeka_analizi(analiz_data, sembol_in)
                rp_calc = risk_profile_params(st.session_state.risk_profile)
                sl, tp1, tp2, lastp, err = yapilandir_tp_sl(
                    analiz_data, yon, rp_calc
                )
                narr = analiz_narratif(
                    analiz_tipi, analiz_data, karar, guven, yon
                )
                st.session_state.last_analysis = {
                    "sembol": sembol_in,
                    "analiz_tipi": analiz_tipi,
                    "karar": karar,
                    "guven": float(guven),
                    "yon": yon,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "lastp": lastp,
                    "err": err,
                    "narrative_md": narr,
                }
            else:
                st.session_state.last_analysis = None
                st.error("Veri çekilemedi veya çerçeve boş.")
        except Exception as ex:
            st.session_state.last_analysis = None
            st.error(f"Analiz hatası: {ex}")
elif run:
    st.error("Teknik / AI modülleri yüklenemedi (.env ve bağımlılıklar).")

la = st.session_state.last_analysis
if la and la.get("sembol") == sembol_in:
    st.markdown(la["narrative_md"], unsafe_allow_html=True)
    st.divider()
    c_a, c_b, c_c, c_d = st.columns(4)
    c_a.metric("AI güven", f"%{la['guven']:.1f}")
    c_b.metric("Yön", la["yon"])
    lp = la.get("lastp")
    c_c.metric("Son fiyat", f"{lp:.6f}" if lp else "—")
    err = la.get("err")
    yon_la = la.get("yon")
    sl, tp1, tp2 = la.get("sl"), la.get("tp1"), la.get("tp2")
    if err:
        st.warning(err)
    elif yon_la in ("LONG", "SHORT") and sl is not None and tp1 and tp2:
        c_d.metric("Önerilen SL", f"{sl:.6f}")
        st.success(
            f"**TP1:** `{tp1:.6f}` | **TP2:** `{tp2:.6f}` — SL yapısal (son swing + ATR)."
        )
        notional = balance * bal_pct * lev if balance else 0
        qty_est = (notional / lp) if lp and lp > 0 else 0
        st.caption(
            f"Tahmini pozisyon (notional ≈ bakiye×%{bal_pct*100:.0f}×kaldıraç): **${notional:,.2f}** | "
            f"Miktar ≈ **{qty_est:.6f}** {sembol_in.replace('USDT', '')}"
        )
        if st.button("🚀 İşleme gir (TP/SL ile)", key="enter_trade"):
            intent = {
                "symbol": sembol_in,
                "side": yon_la,
                "leverage": lev,
                "balance_fraction": bal_pct,
                "margin_mode": "ISOLATED",
                "isolated_only": True,
                "tp1": tp1,
                "tp2": tp2,
                "stop_loss": sl,
                "risk_profile": st.session_state.risk_profile,
                "analysis_type": la.get("analiz_tipi"),
                "ai_confidence": la["guven"],
                "requested_at": datetime.utcnow().isoformat() + "Z",
            }
            os.makedirs("data", exist_ok=True)
            # Gerçek işlemde bot bu dosyayı tüketir; paper modda yazma (kuyruk birikmez).
            if not PAPER_TRADING:
                with open(TRADE_INTENT_PATH, "w", encoding="utf-8") as f:
                    json.dump(intent, f, ensure_ascii=False, indent=2)
            if (
                PAPER_TRADING
                and paper_trader
                and database
                and sl is not None
                and lp
            ):
                if paper_trader.has_open_position(sembol_in):
                    st.warning(f"{sembol_in} için zaten açık pozisyon var.")
                else:
                    qty = (float(balance) * bal_pct * lev) / float(lp)
                    now_iso = pd.Timestamp.now().isoformat()
                    paper_trader.open_position(
                        sembol_in,
                        yon_la,
                        float(lp),
                        qty,
                        float(sl),
                        float(tp1),
                        float(tp2),
                        now_iso,
                    )
                    database.islem_kaydet(
                        sembol_in,
                        {
                            "side": yon_la,
                            "giris_fiyat": float(lp),
                            "miktar": qty,
                            "stop_loss": float(sl),
                            "take_profit": float(tp2),
                            "ai_guven": la["guven"],
                            "neden": f"UI|{la.get('analiz_tipi')}|İzole|{lev}x",
                        },
                    )
                    st.success(
                        f"Paper pozisyon açıldı: **{yon_la}** {sembol_in}. TP/SL kayıtlı."
                    )
                    st.cache_data.clear()
                    time.sleep(0.2)
                    st.rerun()
            else:
                st.info(
                    "`PAPER_TRADING=False` iken niyet dosyası yazıldı; **main.py** döngüsü Binance "
                    "vadeli (izole marjin + SL/TP) emrini birkaç saniye içinde işler. "
                    "API anahtarlarını `.env` içinde doğrulayın."
                )
elif la and la.get("sembol") != sembol_in:
    st.caption(
        f"Son analiz **{la.get('sembol')}** için; giriş kutusundaki pariteyle eşleşmiyor — tekrar **Analizi çalıştır**."
    )

# --- 7/24 tarama & haber ---
st.markdown("### 📡 Otomatik tarama, güven ve haber entegrasyonu")
sc1, sc2 = st.columns([1, 1])
with sc1:
    st.markdown("##### Son AI tahminleri (güven oranı)")
    if not tahminler.empty:
        show = tahminler.head(25).copy()
        if "zaman" in show.columns:
            show["zaman"] = pd.to_datetime(show["zaman"], errors="coerce").dt.strftime(
                "%m-%d %H:%M"
            )
        
        # Güven oranını 0-1 arasına çek (ProgressColumn % gösterimi için)
        if "tahmin_olasilik" in show.columns:
            show["tahmin_olasilik"] = show["tahmin_olasilik"] / 100.0

        st.dataframe(
            show[
                [c for c in ["zaman", "sembol", "tahmin_yon", "tahmin_olasilik"] if c in show.columns]
            ],
            hide_index=True,
            height=320,
            column_config={
                "tahmin_olasilik": st.column_config.ProgressColumn(
                    "Güven %", min_value=0, max_value=1, format="%d%%"
                )
            },
        )
    else:
        st.caption("Bot çalıştıkça tahminler burada birikecek.")
with sc2:
    st.markdown("##### Haber akışı — Etki puanı (0–10, **≥7** güçlü piyasa etkisi varsayımı)")
    haber_satir = haberleri_lite_cek(14)
    if haber_satir:
        for h in haber_satir:
            renk = "#ff6b6b" if h["etki"] >= 7 else "#aaa"
            st.markdown(
                f"<div style='border-left:3px solid {renk};padding-left:10px;margin-bottom:10px'>"
                f"<span style='color:#00ffcc;font-weight:700'>Etki {h['etki']}/10</span> "
                f"<span style='color:#666'>[{h['parite']}]</span><br/>{h['baslik']}<br/>"
                f"<small style='color:#888'>{h['neden']}</small></div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("Haber RSS şu an okunamadı.")

# Orta bölüm grafikler
st.markdown("### 📊 Performans ve varlık analizi")
c1, c2 = st.columns([2, 1])
kapali_islemler = (
    islemler[islemler["durum"] == "KAPALI"] if not islemler.empty else pd.DataFrame()
)
with c1:
    if not islemler.empty and len(kapali_islemler) > 0:
        kap_sorted = kapali_islemler.sort_values("cikis_zamani")
        kap_sorted = kap_sorted.copy()
        kap_sorted["cum_pnl"] = kap_sorted["kar_zarar"].cumsum()
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=kap_sorted["cikis_zamani"],
                y=kap_sorted["cum_pnl"],
                mode="lines",
                name="Equity",
                line=dict(color="#00ffcc", width=3, shape="spline"),
                fill="tozeroy",
                fillcolor="rgba(0, 255, 204, 0.08)",
            )
        )
        fig.update_layout(
            title="Equity eğrisi",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=380,
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Kapanmış işlem birikince performans eğrisi çizilir.")
with c2:
    if not islemler.empty:
        pnl_by_symbol = islemler.groupby("sembol")["kar_zarar"].sum().reset_index()
        fig2 = px.bar(
            pnl_by_symbol,
            x="sembol",
            y="kar_zarar",
            title="Varlık bazlı PnL",
            color="kar_zarar",
            color_continuous_scale=["#ff4b4b", "#00ffcc"],
        )
        fig2.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            height=380,
        )
        st.plotly_chart(fig2, width="stretch")

# Alt sekmeler
tab1, tab2, tab3, tab4 = st.tabs(
    ["🎯 Açık operasyonlar", "📜 Geçmiş", "🧠 AI tahminleri", "🛠️ Sistem logları"]
)

with tab1:
    aciklar = paper_state.get("positions", {})
    if aciklar:
        for sembol, pos in aciklar.items():
            if pos.get("status") in ("OPEN", "TP1"):
                with st.expander(
                    f"📌 {sembol} | {pos['side']} | Giriş: ${pos['entry']:.4f}",
                    expanded=False,
                ):
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Miktar", f"{pos['qty']:.6f}")
                    col2.metric("SL", f"${pos['stop']:.4f}")
                    col3.metric("TP1", f"${pos['tp1']:.4f}")
                    col4.metric("TP2", f"${pos['tp2']:.4f}")
                    st.progress(min(pos.get("bar_count", 0) / 48, 1.0))
    else:
        st.markdown(
            "<div style='text-align:center;padding:40px;color:#555'>Açık operasyon yok.</div>",
            unsafe_allow_html=True,
        )

with tab2:
    if not islemler.empty:
        display_df = islemler.copy()
        if "giris_zamani" in display_df.columns:
            display_df["zaman"] = pd.to_datetime(display_df["giris_zamani"]).dt.strftime(
                "%d/%m %H:%M"
            )
        show_cols = [
            c
            for c in [
                "zaman",
                "sembol",
                "side",
                "giris_fiyat",
                "kar_zarar",
                "durum",
                "neden",
            ]
            if c in display_df.columns
        ]
        st.dataframe(
            display_df[show_cols],
            hide_index=True,
            height=480,
            column_config={
                "giris_fiyat": st.column_config.NumberColumn("Giriş", format="$%.4f"),
                "kar_zarar": st.column_config.NumberColumn("PnL", format="$%.2f"),
            },
        )
    else:
        st.write("Geçmiş boş.")

with tab3:
    if not tahminler.empty:
        col_t1, col_t2 = st.columns([1, 2])
        with col_t1:
            fig_pie = px.pie(
                tahminler,
                names="tahmin_yon",
                title="Yön dağılımı",
                hole=0.55,
                color_discrete_map={
                    "LONG": "#00ffcc",
                    "SHORT": "#ff00ff",
                    "EXIT": "#444",
                },
            )
            fig_pie.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_pie, width="stretch")
        with col_t2:
            st.dataframe(
                tahminler.head(40),
                hide_index=True,
                width="stretch",
            )
    else:
        st.info("AI tahmin kaydı yok.")

with tab4:
    if not loglar.empty:
        for _, row in loglar.head(50).iterrows():
            color = (
                "#00ffcc"
                if row["seviye"] == "INFO"
                else "#ffcc00"
                if row["seviye"] == "WARNING"
                else "#ff4b4b"
            )
            st.markdown(
                f"<div style='font-size:0.85rem;border-left:3px solid {color};padding-left:8px;margin-bottom:4px'>"
                f"{row['zaman']} | {row['seviye']} | [{row['kaynak']}] {row['mesaj']}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.write("Log yok.")

st.markdown("---")
st.markdown(
    "<div style='text-align:center;color:#444;font-size:0.8rem'>BorsaAI PRO | Elite Terminal | 2026</div>",
    unsafe_allow_html=True,
)

if st.checkbox("Canlı sayfa yenileme (10 sn)", value=False):
    st.markdown(
        f"<script>setTimeout(function(){{window.location.reload();}},{10*1000});</script>",
        unsafe_allow_html=True,
    )
