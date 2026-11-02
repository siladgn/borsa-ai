"""
Dashboard'ın yazdığı `data/ui_trade_intent.json` dosyasını tüketir.

Yalnızca PAPER_TRADING=False ve Binance API anahtarları tanımlıyken çalışır.
İzole marjin + kaldıraç + market giriş + reduce-only SL/TP (binance_trader).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "data")
INTENT_PATH = os.path.join(DATA_DIR, "ui_trade_intent.json")
PROCESSING_PATH = INTENT_PATH + ".processing"
MAX_INTENT_AGE_SEC = int(os.getenv("UI_INTENT_MAX_AGE_SEC", "300"))


def _archive_dir() -> str:
    d = os.path.join(DATA_DIR, "trade_intents_archive")
    os.makedirs(d, exist_ok=True)
    return d


def _archive_intent(payload: Dict[str, Any], tag: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fn = os.path.join(_archive_dir(), f"intent_{ts}_{tag}.json")
    try:
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("Intent arşivlenemedi: %s", e)


def _parse_requested_at(iso_s: str) -> Optional[datetime]:
    if not iso_s or not isinstance(iso_s, str):
        return None
    s = iso_s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _spot_last_price(symbol: str) -> float:
    r = requests.get(
        f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
        timeout=15,
    )
    r.raise_for_status()
    return float(r.json()["price"])


def _recover_stale_processing() -> None:
    if not os.path.exists(PROCESSING_PATH):
        return
    age = time.time() - os.path.getmtime(PROCESSING_PATH)
    if age <= MAX_INTENT_AGE_SEC * 2:
        return
    try:
        with open(PROCESSING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"error": "unreadable_processing"}
    _archive_intent(data, "stale_processing")
    try:
        os.remove(PROCESSING_PATH)
    except OSError:
        pass
    logger.warning("ui_trade_intent.json.processing zaman aşımı — arşivlendi.")


def try_process_ui_trade_intent(
    notify: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Bekleyen UI işlem niyetini işler. Başarı/hata mesajı döner; iş yoksa None.
    """
    from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, PAPER_TRADING

    if PAPER_TRADING:
        return None
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        return None

    os.makedirs(DATA_DIR, exist_ok=True)
    _recover_stale_processing()

    if not os.path.exists(INTENT_PATH):
        return None

    try:
        shutil.move(INTENT_PATH, PROCESSING_PATH)
    except OSError as e:
        logger.error("Intent taşınamadı: %s", e)
        return None

    intent: Dict[str, Any]
    try:
        with open(PROCESSING_PATH, "r", encoding="utf-8") as f:
            intent = json.load(f)
    except Exception as e:
        logger.error("Intent JSON okunamadı: %s", e)
        try:
            os.remove(PROCESSING_PATH)
        except OSError:
            pass
        return None

    def _done(tag: str, extra: Optional[Dict[str, Any]] = None) -> None:
        pack = dict(intent)
        if extra:
            pack["_executor"] = extra
        _archive_intent(pack, tag)
        try:
            os.remove(PROCESSING_PATH)
        except OSError:
            pass

    # Zaman aşımı (replay önleme)
    rt = _parse_requested_at(str(intent.get("requested_at", "")))
    if rt is not None:
        now = datetime.now(timezone.utc)
        if rt.tzinfo is None:
            rt = rt.replace(tzinfo=timezone.utc)
        if (now - rt).total_seconds() > MAX_INTENT_AGE_SEC:
            _done("expired", {"reason": "requested_at too old"})
            return "UI intent süresi doldu (yeniden analiz gerekir)."

    symbol = str(intent.get("symbol", "")).upper().strip()
    side = str(intent.get("side", "")).upper().strip()
    if side not in ("LONG", "SHORT") or not symbol.endswith("USDT"):
        _done("invalid", {"reason": "bad symbol or side"})
        return "UI intent geçersiz (symbol/side)."

    if intent.get("margin_mode", "").upper() not in ("", "ISOLATED"):
        _done("invalid", {"reason": "only isolated supported"})
        return "Yalnızca izole marjin desteklenir."

    try:
        lev = max(1, min(125, int(float(intent.get("leverage", 1)))))
    except (TypeError, ValueError):
        lev = 1

    try:
        bf = float(intent.get("balance_fraction", 0.2))
    except (TypeError, ValueError):
        bf = 0.2
    bf = max(0.01, min(1.0, bf))

    try:
        sl = float(intent.get("stop_loss"))
        tp2 = float(intent.get("tp2") or intent.get("take_profit"))
    except (TypeError, ValueError):
        _done("invalid", {"reason": "sl/tp missing"})
        return "SL veya TP eksik / sayı değil."

    if sl <= 0 or tp2 <= 0:
        _done("invalid", {"reason": "sl/tp nonpositive"})
        return "SL/TP pozitif olmalı."

    try:
        from modules.binance_trader import (
            fetch_usdt_futures_free,
            futures_amount_min,
            gercek_islem_ac,
        )

        free_usdt = fetch_usdt_futures_free()
        if free_usdt <= 0:
            _done("no_balance", {"free_usdt": free_usdt})
            return "Vadeli USDT bakiyesi yetersiz veya okunamadı."

        price = _spot_last_price(symbol)
        margin_use = free_usdt * bf
        notional = margin_use * lev
        qty = notional / price
        if qty <= 0:
            _done("qty_zero", {"notional": notional, "price": price})
            return "Hesaplanan miktar sıfır."

        qmin = futures_amount_min(symbol)
        if qmin is not None and qty < qmin:
            _done(
                "below_min_qty",
                {"qty": qty, "min": qmin, "free_usdt": free_usdt},
            )
            msg = (
                f"Miktar borsa minimumunun altında (min {qmin}). "
                f"Bakiye % veya kaldıraç artırın. qty={qty:.8f}"
            )
            if notify:
                notify(msg)
            return msg

        entry, filled, sl_tp_warn = gercek_islem_ac(
            symbol, side, qty, lev, sl, tp2
        )

        from modules import database

        try:
            database.islem_kaydet(
                symbol,
                {
                    "side": side,
                    "giris_fiyat": float(entry),
                    "miktar": float(filled),
                    "stop_loss": sl,
                    "take_profit": tp2,
                    "ai_guven": float(intent.get("ai_confidence", 0) or 0),
                    "neden": f"UI_INTENT|İzole|{lev}x|bf={bf:.3f}",
                },
            )
        except Exception as db_e:
            logger.warning("Pozisyon açıldı ancak DB kaydı başarısız: %s", db_e)

        ok_msg = (
            f"✅ *GERÇEK POZİSYON* — {symbol} {side}\n"
            f"Giriş: `{entry:.6f}` | Miktar: `{filled:.6f}`\n"
            f"Kaldıraç: `{lev}x` | Marjin: *İzole*\n"
            f"SL: `{sl}` | TP: `{tp2}`"
        )
        if sl_tp_warn:
            ok_msg += f"\n⚠️ SL/TP uyarısı: `{sl_tp_warn}`"

        _done(
            "executed",
            {
                "entry": entry,
                "filled": filled,
                "sl_tp_warn": sl_tp_warn,
            },
        )

        if notify:
            notify(ok_msg)
        logger.info("UI trade intent executed: %s %s qty=%s", symbol, side, filled)
        return ok_msg.replace("*", "").replace("`", "")  # log-friendly

    except Exception as e:
        err = str(e)
        logger.exception("UI intent execution failed: %s", err)
        _done("error", {"error": err})
        if notify:
            notify(f"❌ UI işlem hatası ({symbol}): `{err}`")
        return f"UI işlem hatası: {err}"
