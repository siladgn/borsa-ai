import os
import numpy as np
import pandas as pd
import joblib
import warnings
import optuna
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier

warnings.filterwarnings('ignore')

os.makedirs("data/models", exist_ok=True)

# ─── OPTİMİZE İNDİKATÖR SETİ (11 Özellik — Temiz & Güçlü) ───
# Not: Çakışan/gürültülü özellikler çıkarıldı (overfitting riski düşürüldü)
OZELLIKLER = [
    "ema50_sapma",       # Trend yönü ve gücü (EMA20/200 ile çakışma kaldırıldı)
    "macd_hist",         # Momentum değişimi
    "bb_pband",          # Volatilite içindeki konum (0-1)
    "rsi",               # Aşırı alım/satım (stoch_rsi ile çakışma kaldırıldı)
    "obv_degisim",       # Akıllı para akışı (hacim eğimi)
    "vwap_sapma",        # Kurumsal referans fiyat sapması
    "fib_skor",          # Fibonacci destek/direnç konumu
    "ob_tip",            # Order Block yönü (+1/-1)
    "balina_baskisi",    # Büyük oyuncu alım/satım baskısı
    "funding_rate",      # Kalabalık pozisyonu (fonlama oranı)
    "adx_trend_gucu",    # Trend gücü + yön (supertrend ile çakışma kaldırıldı)
    "order_book_imbalance", # Emir defteri dengesizliği
    "spoofing_metric",   # Sahte emir yığılması
]

def hedef_olustur(df, min_hareket=0.003):
    df["gelecek_getiri"] = df["close"].pct_change(2).shift(-2)
    
    df["target"] = np.where(
        df["gelecek_getiri"] >  min_hareket,  1,
        np.where(df["gelecek_getiri"] < -min_hareket, 0, 2)
    )
    return df[df["target"] != 2]

def walk_forward_dogruluk(X, y, n_splits=5):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    rf = RandomForestClassifier(n_estimators=100, max_depth=5, min_samples_split=15, random_state=42, class_weight="balanced")
    skorlar = []
    for train_idx, test_idx in tscv.split(X):
        Xtr, Xte = X[train_idx], X[test_idx]
        ytr, yte = y[train_idx], y[test_idx]
        sc = StandardScaler()
        rf.fit(sc.fit_transform(Xtr), ytr)
        skorlar.append(rf.score(sc.transform(Xte), yte))
        
    if not skorlar: return 0.0, 0.0
    return round(np.mean(skorlar), 4), round(np.std(skorlar), 4)

def model_egit(X_scaled, y):
    def objective(trial):
        rf_n_estimators = trial.suggest_int('rf_n_estimators', 100, 300, step=100)
        rf_max_depth = trial.suggest_int('rf_max_depth', 3, 7)
        gb_learning_rate = trial.suggest_float('gb_learning_rate', 0.01, 0.1, log=True)
        xgb_max_depth = trial.suggest_int('xgb_max_depth', 3, 6)
        
        rf = RandomForestClassifier(n_estimators=rf_n_estimators, max_depth=rf_max_depth, min_samples_split=15, min_samples_leaf=8, random_state=42, class_weight="balanced")
        gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=gb_learning_rate, random_state=42)
        xgb = XGBClassifier(n_estimators=100, max_depth=xgb_max_depth, learning_rate=gb_learning_rate, random_state=42, use_label_encoder=False, eval_metric="logloss", verbosity=0)
        
        ensemble = VotingClassifier(estimators=[("rf", rf), ("gb", gb), ("xgb", xgb)], voting="soft")
        from sklearn.model_selection import cross_val_score
        score = cross_val_score(ensemble, X_scaled, y, cv=3).mean()
        return score

    # Optuna study (Fast optimize)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=5)
    best = study.best_params
    
    rf = RandomForestClassifier(n_estimators=best['rf_n_estimators'], max_depth=best['rf_max_depth'], min_samples_split=15, min_samples_leaf=8, random_state=42, class_weight="balanced")
    gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=best['gb_learning_rate'], random_state=42)
    xgb = XGBClassifier(n_estimators=100, max_depth=best['xgb_max_depth'], learning_rate=best['gb_learning_rate'], random_state=42, use_label_encoder=False, eval_metric="logloss", verbosity=0)
    
    ensemble = VotingClassifier(estimators=[("rf", rf), ("gb", gb), ("xgb", xgb)], voting="soft")
    model = CalibratedClassifierCV(ensemble, cv=3, method="isotonic")
    model.fit(X_scaled, y)
    return model

def yapay_zeka_analizi(data, sembol="GENEL"):
    df = data.copy()
    df = hedef_olustur(df, min_hareket=0.003)
    df = df.dropna()

    # BURASI KRİTİK: Artık 3 değişken dönüyoruz (Karar, Olasılık, Yön)
    if len(df) < 100:
        return "VERİ YETERSİZ", 0.0, "EXIT"

    mevcut = [f for f in OZELLIKLER if f in df.columns]
    if not mevcut:
        return "BEKLE", 0.0, "EXIT"
        
    X = df[mevcut].iloc[:-1].values
    y = df["target"].iloc[:-1].values
    X_tahmin = df[mevcut].iloc[-1:].values

    if len(np.unique(y)) < 2:
        return "BEKLE", 0.0, "EXIT"

    model_dosya  = f"data/models/{sembol.replace('.', '_')}_model.pkl"
    scaler_dosya = f"data/models/{sembol.replace('.', '_')}_scaler.pkl"

    yeniden_egit = True
    if os.path.exists(model_dosya):
        son_degisim = os.path.getmtime(model_dosya)
        import time
        if (time.time() - son_degisim) / 3600 < 4: # Her 4 saatte bir yeniden eğit
            yeniden_egit = False

    if yeniden_egit:
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        model = model_egit(X_scaled, y)
        joblib.dump(model,  model_dosya)
        joblib.dump(scaler, scaler_dosya)
    else:
        model  = joblib.load(model_dosya)
        scaler = joblib.load(scaler_dosya)
        X_scaled = scaler.transform(X)

    X_tahmin_scaled = scaler.transform(X_tahmin)
    gercek_dogruluk, dogruluk_std = walk_forward_dogruluk(X, y)

    if len(model.classes_) < 2:
        return "BEKLE", 0.0, "EXIT"

    olasilik = model.predict_proba(X_tahmin_scaled)[0][1] * 100

    son = df.iloc[-1]
    ma20 = son.get("ma20", son["close"])
    ma50 = son.get("ma50", son["close"])
    adx = son.get("adx", 20)
    rsi = son.get("rsi", 50)
    volume_ratio = son.get("volume_ratio", 1.0)

    trend_yukari = son["close"] > ma50 and ma20 >= ma50
    trend_asagi = son["close"] < ma50 and ma20 <= ma50
    hacim_onay = volume_ratio > 1.15
    momentum_long = rsi >= 52 and adx >= 18
    momentum_short = rsi <= 48 and adx >= 18

    if gercek_dogruluk < 0.50:
        # Artık 0.0 yerine gerçek anlık olasılığı döndürüyoruz
        return f"GÜVEN DÜŞÜK (%{gercek_dogruluk*100:.1f})", round(olasilik, 2), "EXIT"

    yon = "EXIT"
    if olasilik >= 70.0 and trend_yukari and hacim_onay and momentum_long:
        karar = "KESİN AL"
        yon = "LONG"
        guven = olasilik
    elif olasilik >= 65.0 and trend_yukari and hacim_onay and momentum_long:
        karar = "POTANSİYEL AL"
        yon = "LONG"
        guven = olasilik
    elif olasilik <= 30.0 and trend_asagi and hacim_onay and momentum_short:
        karar = "SAT / UZAK DUR"
        yon = "SHORT"
        guven = 100 - olasilik # SHORT için düşüş ihtimalini göster
    elif olasilik <= 35.0 and trend_asagi and hacim_onay and momentum_short:
        karar = "POTANSİYEL SAT"
        yon = "SHORT"
        guven = 100 - olasilik
    else:
        karar = "BEKLE / PUSUDA"
        yon = "EXIT"
        guven = olasilik

    # Geriye tam olarak main.py'nin beklediği 3 değeri gönderiyoruz
    return karar, round(guven, 2), yon