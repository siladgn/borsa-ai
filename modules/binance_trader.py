import ccxt
from typing import Any, Dict, Optional, Tuple

from config import BINANCE_API_KEY, BINANCE_SECRET_KEY


def _get_exchange():
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        raise ValueError("Binance API anahtarları eksik! .env dosyasını kontrol edin.")

    return ccxt.binance(
        {
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_SECRET_KEY,
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
            },
        }
    )


def futures_amount_min(sembol: str) -> Optional[float]:
    """Sözleşme için minimum emir miktarı (baz coin); yoksa None."""
    exchange = _get_exchange()
    exchange.load_markets()
    market_symbol = sembol.replace("USDT", "/USDT")
    m = exchange.markets.get(market_symbol)
    if not m:
        return None
    mn = m.get("limits", {}).get("amount", {}).get("min")
    if mn is None:
        return None
    return float(mn)


def fetch_usdt_futures_free() -> float:
    """USDT-M vadeli cüzdanında kullanılabilir USDT (tahmini pozisyon marjı için)."""
    ex = _get_exchange()
    ex.load_markets()
    bal = ex.fetch_balance({"type": "future"})
    usdt = bal.get("USDT") or {}
    free = usdt.get("free")
    if free is None:
        return 0.0
    return float(free)

def _futures_order_params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Mark fiyatına göre tetik — ani wick'te gereksiz tetik riskini azaltır."""
    p: Dict[str, Any] = {"workingType": "MARK_PRICE"}
    if extra:
        p.update(extra)
    return p


def gercek_islem_ac(
    sembol: str,
    side: str,
    miktar: float,
    kaldirac: int,
    stop_loss: float,
    take_profit: float,
) -> Tuple[float, float, Optional[str]]:
    """
    İzole marjin + market giriş + reduce-only SL / TP (TP2 seviyesi).
    Dönüş: (giriş_fiyatı, gerçekleşen miktar, sl_tp_uyarı veya None)
    """
    exchange = _get_exchange()
    exchange.load_markets()

    market_symbol = sembol.replace("USDT", "/USDT")

    # 1) Önce izole marjin (Cross kapalı)
    try:
        exchange.set_margin_mode("isolated", market_symbol)
    except Exception:
        pass

    # 2) Kaldıraç
    try:
        exchange.set_leverage(int(kaldirac), market_symbol)
    except Exception as e:
        print(f"[{sembol}] Kaldıraç ayarlanırken uyarı: {e}")

    formatted_amount = float(exchange.amount_to_precision(market_symbol, miktar))
    if formatted_amount <= 0:
        raise ValueError(f"Geçersiz miktar: {miktar} -> {formatted_amount}")

    sl_price = float(exchange.price_to_precision(market_symbol, stop_loss))
    tp_price = float(exchange.price_to_precision(market_symbol, take_profit))

    order_side = "buy" if side == "LONG" else "sell"
    ana_emir = exchange.create_market_order(market_symbol, order_side, formatted_amount)
    giris_fiyati = float(
        ana_emir.get("average") or ana_emir.get("price") or 0.0
    )
    filled = float(
        ana_emir.get("filled") or ana_emir.get("amount") or formatted_amount
    )

    karsi_side = "sell" if side == "LONG" else "buy"
    filled_amt = float(exchange.amount_to_precision(market_symbol, filled))
    try:
        exchange.create_order(
            symbol=market_symbol,
            type="STOP_MARKET",
            side=karsi_side,
            amount=filled_amt,
            price=None,
            params=_futures_order_params(
                {"stopPrice": sl_price, "reduceOnly": True}
            ),
        )
        exchange.create_order(
            symbol=market_symbol,
            type="TAKE_PROFIT_MARKET",
            side=karsi_side,
            amount=filled_amt,
            price=None,
            params=_futures_order_params(
                {"stopPrice": tp_price, "reduceOnly": True}
            ),
        )
    except Exception as e:
        msg = f"{e}"
        print(f"[{sembol}] SL/TP Emirleri kurulamadı: {msg}")
        return giris_fiyati, filled_amt, msg

    return giris_fiyati, filled_amt, None

def gercek_islem_kapat(sembol):
    exchange = _get_exchange()
    exchange.load_markets()
    market_symbol = sembol.replace("USDT", "/USDT")
    
    # Önce açık olan Stop ve TP emirlerini iptal et
    try:
        exchange.cancel_all_orders(market_symbol)
    except:
        pass
        
    # Mevcut açık pozisyonu bul ve tam tersi işlemle kapat (Reduce Only Market)
    try:
        positions = exchange.fetch_positions([market_symbol])
        for p in positions:
            if float(p['info']['positionAmt']) != 0:
                miktar = abs(float(p['info']['positionAmt']))
                kapanis_yonu = 'sell' if float(p['info']['positionAmt']) > 0 else 'buy'
                exchange.create_market_order(market_symbol, kapanis_yonu, miktar, params={"reduceOnly": True})
    except Exception as e:
        raise Exception(f"Binance pozisyon kapatılamadı: {e}")