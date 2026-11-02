import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple
import pandas as pd


# Çalışma klasörü değişse bile aynı projedeki `data/` klasörünü kullan
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # borsaai/
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "paper_state.json")
TRADES_CSV = os.path.join(DATA_DIR, "paper_trades.csv")

def _fmt_money(x: float) -> str:
    x = float(x)
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):.2f}"


@dataclass
class Position:
    symbol: str
    side: str  # "LONG" | "SHORT"
    entry: float
    qty: float
    stop: float
    tp1: float
    tp2: float
    opened_at: str
    status: str = "OPEN"  # OPEN | TP1 | CLOSED
    remaining_qty: float = 0.0
    highest_price: float = 0.0 # Trailing için tepe fiyat
    lowest_price: float = 999999.0 # Trailing için dip fiyat (Short)
    be_active: bool = False # Break-even tetiklendi mi?
    bar_count: int = 0 # Pozisyon kaç bardır açık?

    def __post_init__(self):
        if self.remaining_qty == 0.0:
            self.remaining_qty = float(self.qty)
        if self.highest_price == 0.0:
            self.highest_price = float(self.entry)
        if self.lowest_price == 999999.0:
            self.lowest_price = float(self.entry)


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_state() -> dict:
    _ensure_data_dir()
    if not os.path.exists(STATE_PATH):
        return {"balance": None, "positions": {}, "last_daily_report": None, "last_weekly_report": None, "daily_pnl": 0.0, "last_pnl_reset": None, "consecutive_losses": 0, "halt_until": None}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
            # Yeni alanlar yoksa ekle
            if "daily_pnl" not in state: state["daily_pnl"] = 0.0
            if "last_pnl_reset" not in state: state["last_pnl_reset"] = None
            if "consecutive_losses" not in state: state["consecutive_losses"] = 0
            if "halt_until" not in state: state["halt_until"] = None
            return state
    except Exception:
        return {"balance": None, "positions": {}, "last_daily_report": None, "last_weekly_report": None, "daily_pnl": 0.0, "last_pnl_reset": None, "consecutive_losses": 0, "halt_until": None}

def check_circuit_breaker(max_loss_percent: float = 0.05) -> bool:
    """Günlük kaybın bakiye bazında %X'i geçip geçmediğini veya Panic Break olup olmadığını kontrol eder."""
    state = _load_state()
    now_ts = pd.Timestamp.now()
    now_str = now_ts.strftime("%Y-%m-%d")
    
    # 1. Panic Break Kontrolü (Üst üste kayıp)
    if state.get("halt_until"):
        halt_until = pd.to_datetime(state["halt_until"])
        if now_ts < halt_until:
            return True # Sistem hala durdurulmuş durumda
    
    # 2. Yeni günse günlük PnL resetle
    if state.get("last_pnl_reset") != now_str:
        state["daily_pnl"] = 0.0
        state["last_pnl_reset"] = now_str
        _save_state(state)
        return False
    
    balance = float(state.get("balance", 1000))
    daily_pnl = float(state.get("daily_pnl", 0.0))
    
    if daily_pnl < 0 and abs(daily_pnl) >= (balance * max_loss_percent):
        return True # Devre kesici aktif!
    return False


def _save_state(state: dict) -> None:
    _ensure_data_dir()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def init_balance_if_needed(start_balance: float) -> float:
    state = _load_state()
    if state.get("balance") is None:
        state["balance"] = float(start_balance)
        _save_state(state)
    return float(state["balance"])


def get_balance(default: float) -> float:
    state = _load_state()
    b = state.get("balance")
    return float(default) if b is None else float(b)


def has_open_position(symbol: str) -> bool:
    state = _load_state()
    pos = state.get("positions", {}).get(symbol)
    return bool(pos) and pos.get("status") in ("OPEN", "TP1")

def count_open_positions() -> int:
    state = _load_state()
    positions: Dict[str, dict] = state.get("positions", {})
    return sum(1 for p in positions.values() if p and p.get("status") in ("OPEN", "TP1"))


def get_open_position(symbol: str) -> Optional[dict]:
    state = _load_state()
    pos = state.get("positions", {}).get(symbol)
    if not pos or pos.get("status") not in ("OPEN", "TP1"):
        return None
    return pos


def open_position(
    symbol: str,
    side: str,
    entry: float,
    qty: float,
    stop: float,
    tp1: float,
    tp2: float,
    opened_at: str,
) -> None:
    state = _load_state()
    state.setdefault("positions", {})
    p = Position(
        symbol=symbol,
        side=side,
        entry=float(entry),
        qty=float(qty),
        stop=float(stop),
        tp1=float(tp1),
        tp2=float(tp2),
        opened_at=str(opened_at),
    )
    state["positions"][symbol] = asdict(p)
    _save_state(state)


def _append_trade_row(row: dict) -> None:
    _ensure_data_dir()
    df = pd.DataFrame([row])
    header = not os.path.exists(TRADES_CSV)
    df.to_csv(TRADES_CSV, mode="a", header=header, index=False)

def _read_trades_df() -> Optional[pd.DataFrame]:
    if not os.path.exists(TRADES_CSV):
        return None
    try:
        df = pd.read_csv(TRADES_CSV)
        if df.empty or "time" not in df.columns:
            return None
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"])
        return df
    except Exception:
        return None

def _calc_max_drawdown(equity: pd.Series) -> float:
    if equity is None or equity.empty:
        return 0.0
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max.replace(0, pd.NA)
    dd = dd.dropna()
    if dd.empty:
        return 0.0
    return float(dd.min())  # negatif

def build_performance_report(days: int) -> Optional[str]:
    df = _read_trades_df()
    if df is None:
        return None

    cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=days)
    period = df[df["time"] >= cutoff].copy()
    if period.empty:
        return None

    # "trade" sayımı: kapanış eventleri
    close_events = period[period["event"].astype(str).str.contains("CLOSE", na=False)]
    close_events = close_events[~close_events["event"].astype(str).str.contains("TP1_PARTIAL", na=False)]
    trade_count = int(len(close_events))

    pnl_sum = float(period.get("pnl", pd.Series(dtype=float)).fillna(0).sum())
    wins = int((close_events.get("pnl", pd.Series(dtype=float)).fillna(0) > 0).sum())
    win_rate = 0.0 if trade_count == 0 else (wins / trade_count) * 100.0

    # equity ve drawdown: tüm seri üzerinden bakmak daha doğru, ama burada period içindeki bakiyeyi kullanıyoruz
    equity = period.get("balance")
    mdd = 0.0
    if equity is not None and not equity.empty:
        mdd = _calc_max_drawdown(equity.astype(float))

    return (
        f"📊 <b>PAPER PERFORMANCE ({days}g)</b>\n"
        f"- İşlem: <code>{trade_count}</code> | Win-rate: <code>%{win_rate:.1f}</code>\n"
        f"- Net PnL: <code>{_fmt_money(pnl_sum)}</code>\n"
        f"- Max Drawdown: <code>%{abs(mdd)*100:.2f}</code>"
    )

def maybe_send_reports(send_fn, daily: bool = True, weekly: bool = True, target_hour: int = 20) -> None:
    """
    Günlük/haftalık raporu aynı gün içinde belirtilen saatten (target_hour) sonra 1 kere gönderir.
    Varsayılan: Akşam 20:00 (target_hour = 20). Sabah 8 istersen 8 yapabilirsin.
    """
    state = _load_state()
    now = pd.Timestamp.now()
    today = now.strftime("%Y-%m-%d")
    week = f"{now.isocalendar().year}-W{int(now.isocalendar().week):02d}"

    if daily and state.get("last_daily_report") != today and now.hour >= target_hour:
        msg = build_performance_report(1)
        if msg:
            send_fn(msg)
        state["last_daily_report"] = today

    if weekly and state.get("last_weekly_report") != week and now.hour >= target_hour:
        msg = build_performance_report(7)
        if msg:
            send_fn(msg)
        state["last_weekly_report"] = week

    _save_state(state)


def _pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
    if side == "LONG":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


def close_position(symbol: str, last_price: float, now_iso: str, reason: str) -> Tuple[Optional[str], Optional[dict]]:
    """
    Açık pozisyonu anlık fiyattan kapatır (SAT sinyali gibi).
    Döndürür: (telegram_mesajı, event_dict)
    """
    state = _load_state()
    positions: Dict[str, dict] = state.get("positions", {})
    raw = positions.get(symbol)
    if not raw:
        return None, None

    pos = Position(**raw)
    if pos.status not in ("OPEN", "TP1"):
        return None, None

    price = float(last_price)
    exit_qty = pos.remaining_qty
    pnl = _pnl(pos.side, pos.entry, price, exit_qty)
    state["balance"] = float(get_balance(0.0) + pnl)
    state["daily_pnl"] = float(state.get("daily_pnl", 0.0) + pnl) # Günlük PnL'e ekle
    pos.remaining_qty = 0.0
    pos.status = "CLOSED"
    positions[symbol] = asdict(pos)
    _save_state(state)

    _append_trade_row(
        {
            "time": now_iso,
            "symbol": symbol,
            "side": pos.side,
            "event": f"MANUAL_CLOSE_{reason}",
            "entry": pos.entry,
            "exit": price,
            "qty": exit_qty,
            "pnl": pnl,
            "balance": state["balance"],
        }
    )
    return (
        f"📤 <b>PAPER {pos.side} KAPANDI ({reason})</b> — {symbol}\n"
        f"Giriş: <code>{pos.entry:.2f}</code> | Çıkış: <code>{price:.2f}</code> | Adet: <code>{exit_qty:.4f}</code>\n"
        f"PnL: <code>{_fmt_money(pnl)}</code> | Yeni Bakiye: <code>${state['balance']:.2f}</code>",
        {"type": "CLOSE", "reason": reason, "pnl": pnl, "balance": state["balance"]},
    )


def update_position(
    symbol: str,
    last_price: float,
    now_iso: str,
    high: Optional[float] = None,
    low: Optional[float] = None,
    atr: Optional[float] = None, # ATR eklendi
) -> Tuple[Optional[str], Optional[dict]]:
    """
    Açık pozisyon varsa stop/TP kontrol eder.
    Döndürür: (telegram_mesajı, event_dict)
    """
    state = _load_state()
    positions: Dict[str, dict] = state.get("positions", {})
    raw = positions.get(symbol)
    if not raw:
        return None, None

    pos = Position(**raw)
    if pos.status not in ("OPEN", "TP1"):
        return None, None
    
    pos.bar_count += 1 # Bir bar daha geçti

    close_price = float(last_price)
    hi = close_price if high is None else float(high)
    lo = close_price if low is None else float(low)

    # --- 1. TEPE/DİP GÜNCELLEME ---
    if pos.side == "LONG":
        if hi > pos.highest_price: pos.highest_price = hi
    else:
        if lo < pos.lowest_price: pos.lowest_price = lo

    # --- 2. BREAK-EVEN (BE) KONTROLÜ ---
    # Fiyat %0.3 kâra ulaşınca stopu girişe çek (Kârı erkenden koru)
    be_threshold = 0.003 
    if not pos.be_active:
        current_gain = (hi - pos.entry) / pos.entry if pos.side == "LONG" else (pos.entry - lo) / pos.entry
        if current_gain >= be_threshold:
            pos.stop = pos.entry
            pos.be_active = True

    # --- 3. DİNAMİK TRAILING STOP (TS) KONTROLÜ ---
    # Kâr arttıkça takip mesafesini daralt
    current_gain_max = (pos.highest_price - pos.entry) / pos.entry if pos.side == "LONG" else (pos.entry - pos.lowest_price) / pos.entry
    
    ts_activation = 0.015 # %1.5 kârda başla
    ts_dist = 0.010       # %1.0 takip mesafesi
    
    if current_gain_max >= 0.10: # %10 kârda mesafeyi daralt
        ts_dist = 0.005 # %0.5
    elif current_gain_max >= 0.05: # %5 kârda mesafeyi daralt
        ts_dist = 0.007 # %0.7

    if current_gain_max >= ts_activation:
        if pos.side == "LONG":
            # ATR varsa ATR tabanlı, yoksa yüzde tabanlı takip
            new_stop = pos.highest_price - (atr * 1.5) if atr else pos.highest_price * (1 - ts_dist)
            if new_stop > pos.stop: pos.stop = new_stop
        else:
            new_stop = pos.lowest_price + (atr * 1.5) if atr else pos.lowest_price * (1 + ts_dist)
            if new_stop < pos.stop: pos.stop = new_stop

    # --- 4. HİT KONTROLÜ ---
    if pos.side == "LONG":
        stop_hit = lo <= pos.stop
        tp1_hit = hi >= pos.tp1
        tp2_hit = hi >= pos.tp2
    else:
        stop_hit = hi >= pos.stop
        tp1_hit = lo <= pos.tp1
        tp2_hit = lo <= pos.tp2

    # Stop Kontrolü (Konservatif: Önce STOP)
    if stop_hit:
        exit_qty = pos.remaining_qty
        exit_price = float(pos.stop)
        pnl = _pnl(pos.side, pos.entry, exit_price, exit_qty)
        state["balance"] = float(get_balance(0.0) + pnl)
        state["daily_pnl"] = float(state.get("daily_pnl", 0.0) + pnl)
        pos.remaining_qty = 0.0
        pos.status = "CLOSED"
        positions[symbol] = asdict(pos)
        _save_state(state)

        _append_trade_row({
            "time": now_iso, "symbol": symbol, "side": pos.side,
            "event": "STOP_CLOSE", "entry": pos.entry, "exit": exit_price,
            "qty": exit_qty, "pnl": pnl, "balance": state["balance"]
        })
        return (
            f"🛑 <b>PAPER {pos.side} STOP OLDU</b> — {symbol}\n"
            f"Giriş: <code>{pos.entry:.2f}</code> | Çıkış: <code>{exit_price:.2f}</code> | PnL: <code>{_fmt_money(pnl)}</code>",
            {"type": "CLOSE", "reason": "STOP", "pnl": pnl, "balance": state["balance"]}
        )

    # TP2 Kontrolü
    if tp2_hit:
        exit_qty = pos.remaining_qty
        exit_price = float(pos.tp2)
        pnl = _pnl(pos.side, pos.entry, exit_price, exit_qty)
        state["balance"] = float(get_balance(0.0) + pnl)
        state["daily_pnl"] = float(state.get("daily_pnl", 0.0) + pnl)
        pos.remaining_qty = 0.0
        pos.status = "CLOSED"
        positions[symbol] = asdict(pos)
        _save_state(state)

        _append_trade_row({
            "time": now_iso, "symbol": symbol, "side": pos.side,
            "event": "TP2_CLOSE", "entry": pos.entry, "exit": exit_price,
            "qty": exit_qty, "pnl": pnl, "balance": state["balance"]
        })
        return (
            f"✅ <b>PAPER {pos.side} KAPANDI (TP2)</b> — {symbol}\n"
            f"Giriş: <code>{pos.entry:.2f}</code> | Çıkış: <code>{exit_price:.2f}</code> | PnL: <code>{_fmt_money(pnl)}</code>",
            {"type": "CLOSE", "reason": "TP2", "pnl": pnl, "balance": state["balance"]}
        )

    # TP1 Kontrolü (Yarısını kapa, Stopu girişe çek)
    if pos.status == "OPEN" and tp1_hit:
        exit_qty = pos.qty * 0.5
        exit_price = float(pos.tp1)
        pnl = _pnl(pos.side, pos.entry, exit_price, exit_qty)
        state["balance"] = float(get_balance(0.0) + pnl)
        state["daily_pnl"] = float(state.get("daily_pnl", 0.0) + pnl)
        pos.remaining_qty -= exit_qty
        pos.status = "TP1"
        pos.stop = pos.entry # Break-even
        pos.be_active = True
        positions[symbol] = asdict(pos)
        _save_state(state)

        _append_trade_row({
            "time": now_iso, "symbol": symbol, "side": pos.side,
            "event": "TP1_PARTIAL", "entry": pos.entry, "exit": exit_price,
            "qty": exit_qty, "pnl": pnl, "balance": state["balance"]
        })
        return (
            f"🎯 <b>PAPER {pos.side} TP1 GELDİ</b> — {symbol}\n"
            f"Kısmi Çıkış: <code>{exit_price:.2f}</code> | PnL: <code>{_fmt_money(pnl)}</code> | Stop Girişe Çekildi.",
            {"type": "TP1", "pnl": pnl, "balance": state["balance"]}
        )

    # --- 5. TIME-STOP KONTROLÜ (32 Bar / 8 Saat) ---
    max_bars = 32
    if pos.bar_count >= max_bars:
        # Kârdaysak kapa, zarardaysak şans tanı (Veya her türlü kapa)
        current_pnl = _pnl(pos.side, pos.entry, close_price, pos.remaining_qty)
        if current_pnl > 0:
            return close_position(symbol, close_price, now_iso, "TIME_STOP_WIN")
        # Eğer zarardaysak ama SL hit değilse bekleyebiliriz veya 72 barda zorla kapatabiliriz
        if pos.bar_count >= 72:
            return close_position(symbol, close_price, now_iso, "TIME_STOP_FORCE")

    # Değişiklik varsa kaydet (Trailing Stop/BE güncellemeleri için)
    positions[symbol] = asdict(pos)
    _save_state(state)
    return None, None
