import os
import json
import sqlite3
import requests
import feedparser
from typing import Any, Dict, List, Optional
from datetime import datetime

import pandas as pd
import numpy as np
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN
from pydantic import BaseModel

app = FastAPI(title="BorsaAI API", version="1.0.0")

# API Anahtarı Ayarları
API_KEY = os.getenv("API_KEY", "borsaai_secret_key_789")
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header == API_KEY:
        return api_key_header
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN, detail="API Key geçersiz!"
    )

# CORS Ayarları
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE_PATH = "data/paper_state.json"
TRADES_CSV = "data/paper_trades.csv"
DB_PATH = "data/borsa_ai.db"

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

# Modülleri içe aktar
try:
    from modules.teknik import kapsamli_veri_cek, korku_acgozluluk_getir
    from modules.ai_model import yapay_zeka_analizi
    from modules import database, paper_trader
except Exception as e:
    print(f"API Modül yükleme hatası: {e}")

# Helper Functions
def get_trending_usdt_market_helper():
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

def get_market_heatmap_helper():
    symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "AVAXUSDT", "ADAUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT",
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

def etki_puani_baslik(baslik: str) -> tuple[int, list[str]]:
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

def haberleri_lite_cek_helper(limit: int = 12):
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
    if data is None or data.empty:
        return "Veri yok."
    row = data.iloc[-1]
    rsi = float(row.get("rsi", 50))
    adx = float(row.get("adx", 20))
    vr = float(row.get("volume_ratio", 1.0))
    parts = [f"**{ANALIZ_TIPLERI.get(tip, tip)}** | AI: *{karar}* (%{guven:.0f} güven, {yon})."]
    if tip == "genel":
        parts.append(f"RSI {rsi:.1f}, ADX {adx:.1f} — trend gücü ve aşırı alım/satım bölgesi birlikte okunmalı.")
    elif tip == "trend":
        parts.append(f"ADX {adx:.1f} ile trend filtresi; EMA/Supertrend hizası son mumlarda yapısal yönü destekliyor.")
    elif tip == "hacim":
        parts.append(f"Hacim oranı (vol/ort) ~{vr:.2f} — fiyat-hacim uyumu ve OBV eğimi kritik.")
    elif tip == "scalp":
        parts.append("Kısa periyot: dar bant sıkışması ve son 3–5 mumun kapanış kalitesi öne çıkar.")
    elif tip == "vol_kirilim":
        squeeze = int(row.get("bb_squeeze", 0) or 0)
        parts.append("Bollinger sıkışması / genişleme ve ATR ile kırılım ihtimali değerlendirildi." + (" Sıkışma sinyali var." if squeeze else ""))
    elif tip == "balina":
        b = float(row.get("balina_baskisi", 0) or 0)
        parts.append(f"Emir defteri / balina baskısı skoru: {b:.3f} (büyük duvarlara dikkat).")
    elif tip == "orderflow":
        ob = int(row.get("ob_tip", 0) or 0)
        parts.append(f"Order block tipi {ob:+d}, VWAP sapması — kurumsal akışa yakın seviyeler izlenmeli.")
    parts.append("Dominans, toplam grafik ve osilatör taraması bot döngüsünde sürekli; burada seçilen parite anlık özetlenir.")
    return "\n\n".join(parts)

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

# Endpoints
@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}

@app.get("/global-stats")
def global_stats(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    try:
        fng = korku_acgozluluk_getir()
    except Exception:
        fng = 0.50
    return {
        "fear_greed": fng * 100,
        "market_status": "Bullish" if fng > 0.6 else ("Bearish" if fng < 0.4 else "Neutral")
    }

@app.get("/analysis/{symbol}")
def get_analysis(symbol: str, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    try:
        data = kapsamli_veri_cek(symbol)
        if data is None or data.empty:
            raise HTTPException(status_code=404, detail="Veri bulunamadı")
        
        karar, guven, yon = yapay_zeka_analizi(data, symbol)
        last_price = data.iloc[-1]['close']
        
        return {
            "symbol": symbol,
            "karar": karar,
            "guven": guven,
            "yon": yon,
            "price": last_price,
            "timestamp": pd.Timestamp.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analysis-full/{symbol}")
def get_analysis_full(
    symbol: str, 
    risk_profile: str = "normal", 
    analysis_type: str = "genel", 
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    try:
        data = kapsamli_veri_cek(symbol, interval="15m", n_bars=800)
        if data is None or data.empty:
            raise HTTPException(status_code=404, detail="Veri bulunamadı")
            
        karar, guven, yon = yapay_zeka_analizi(data, symbol)
        last_price = data.iloc[-1]['close']
        
        rp_calc = risk_profile_params(risk_profile)
        sl, tp1, tp2, lastp, err = yapilandir_tp_sl(data, yon, rp_calc)
        narr = analiz_narratif(analysis_type, data, karar, guven, yon)
        
        return {
            "symbol": symbol,
            "karar": karar,
            "guven": float(guven),
            "yon": yon,
            "price": last_price,
            "timestamp": pd.Timestamp.now().isoformat(),
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "err": err,
            "narrative": narr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/trending")
def get_trending(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    gainers, losers, vol_leaders = get_trending_usdt_market_helper()
    return {
        "gainers": gainers.to_dict(orient="records") if not gainers.empty else [],
        "losers": losers.to_dict(orient="records") if not losers.empty else [],
        "vol_leaders": vol_leaders.to_dict(orient="records") if not vol_leaders.empty else []
    }

@app.get("/market-heatmap")
def get_heatmap(api_key: str = Depends(get_api_key)) -> List[Dict[str, Any]]:
    df = get_market_heatmap_helper()
    return df.to_dict(orient="records") if not df.empty else []

@app.get("/news")
def get_news(limit: int = 15, api_key: str = Depends(get_api_key)) -> List[Dict[str, Any]]:
    return haberleri_lite_cek_helper(limit)

@app.get("/logs")
def get_logs(limit: int = 100, api_key: str = Depends(get_api_key)) -> List[Dict[str, Any]]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM loglar ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []

@app.get("/paper/state")
def paper_state(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {"balance": None, "positions": {}}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

@app.get("/paper/trades")
def paper_trades(limit: int = 100, api_key: str = Depends(get_api_key)) -> List[Dict[str, Any]]:
    if not os.path.exists(TRADES_CSV):
        return []
    try:
        df = pd.read_csv(TRADES_CSV).tail(limit)
        return df.to_dict(orient="records")
    except Exception:
        return []

@app.get("/database-stats")
def database_stats(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    try:
        state = paper_trader._load_state()
        balance = float(state.get("balance") or 1000.0)
        daily_pnl = float(state.get("daily_pnl") or 0.0)
        
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM islemler WHERE durum = 'ACIK'")
            open_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM islemler")
            total_trades = cursor.fetchone()[0]
            
            cursor.execute("SELECT * FROM islemler WHERE durum = 'KAPALI' ORDER BY cikis_zamani ASC")
            closed_rows = [dict(row) for row in cursor.fetchall()]
            
        wins = sum(1 for r in closed_rows if (r["kar_zarar"] or 0) > 0)
        win_rate = (wins / len(closed_rows)) * 100 if closed_rows else 0.0
        net_pnl = sum(r["kar_zarar"] or 0 for r in closed_rows)
        
        equity_curve = []
        cum_pnl = 0.0
        for r in closed_rows:
            cum_pnl += (r["kar_zarar"] or 0)
            equity_curve.append({
                "time": r["cikis_zamani"],
                "pnl": cum_pnl
            })
            
        # Resolve live prices for active positions
        positions_resolved = {}
        for symbol, pos in state.get("positions", {}).items():
            if pos.get("status") in ("OPEN", "TP1"):
                last_price = binance_last_price(symbol)
                pos_copy = dict(pos)
                pos_copy["last_price"] = last_price or pos.get("entry", 0.0)
                positions_resolved[symbol] = pos_copy
            else:
                positions_resolved[symbol] = pos
            
        return {
            "balance": balance,
            "daily_pnl": daily_pnl,
            "open_positions_count": open_count,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "equity_curve": equity_curve,
            "positions": positions_resolved
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class OpenPositionRequest(BaseModel):
    symbol: str
    side: str
    leverage: int
    balance_fraction: float
    tp1: float
    tp2: float
    sl: float
    risk_profile: str
    analysis_type: str
    ai_confidence: float

@app.post("/paper/open")
def paper_open(req: OpenPositionRequest, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    try:
        if paper_trader.has_open_position(req.symbol):
            raise HTTPException(status_code=400, detail=f"{req.symbol} için zaten açık pozisyon var.")
            
        state = paper_trader._load_state()
        balance = float(state.get("balance") or 1000.0)
        
        lp = binance_last_price(req.symbol)
        if not lp:
            raise HTTPException(status_code=400, detail="Sembol fiyatı Binance'ten alınamadı.")
            
        qty = (balance * req.balance_fraction * req.leverage) / lp
        now_iso = pd.Timestamp.now().isoformat()
        
        paper_trader.open_position(
            req.symbol,
            req.side,
            lp,
            qty,
            req.sl,
            req.tp1,
            req.tp2,
            now_iso
        )
        
        neden_ozet = f"UI|{req.analysis_type}|İzole|{req.leverage}x|{req.risk_profile}"
        database.islem_kaydet(
            req.symbol,
            {
                "side": req.side,
                "giris_fiyat": lp,
                "miktar": qty,
                "stop_loss": req.sl,
                "take_profit": req.tp2,
                "ai_guven": req.ai_confidence,
                "neden": neden_ozet
            }
        )
        return {"status": "success", "message": f"Paper position opened for {req.symbol}", "qty": qty, "price": lp}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ClosePositionRequest(BaseModel):
    symbol: str
    reason: str

@app.post("/paper/close")
def paper_close(req: ClosePositionRequest, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    try:
        state = paper_trader._load_state()
        positions = state.get("positions", {})
        pos = positions.get(req.symbol)
        
        if not pos or pos.get("status") not in ("OPEN", "TP1"):
            raise HTTPException(status_code=404, detail=f"{req.symbol} için açık pozisyon bulunamadı.")
            
        lp = binance_last_price(req.symbol)
        if not lp:
            raise HTTPException(status_code=400, detail="Sembol fiyatı Binance'ten alınamadı.")
            
        now_iso = pd.Timestamp.now().isoformat()
        msg, event_data = paper_trader.close_position(req.symbol, lp, now_iso, req.reason)
        
        if event_data and event_data.get("type") == "CLOSE":
            database.islem_kapat(req.symbol, lp, event_data.get("pnl", 0))
            
        return {"status": "success", "message": f"Paper position closed for {req.symbol}", "pnl": event_data.get("pnl", 0) if event_data else 0}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market-price/{symbol}")
def get_market_price(symbol: str, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    price = binance_last_price(symbol)
    if price is None:
        raise HTTPException(status_code=404, detail="Price not found")
    return {"symbol": symbol, "price": price}
