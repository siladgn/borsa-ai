import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from modules.teknik import kapsamli_veri_cek
import warnings

warnings.filterwarnings('ignore')

def beyni_oku(sembol):
    print(f"\n🧠 [{sembol}] Yapay Zekanın Beyni Okunuyor...")
    data = kapsamli_veri_cek(sembol)
    if data is None or data.empty: return
    df = data.copy()
    df["gelecek_getiri"] = df["close"].pct_change().shift(-1)
    df["target"] = (df["gelecek_getiri"] > 0).astype(int)
    df = df.dropna()

    from modules.ai_model import OZELLIKLER as ozellikler
    X = df[ozellikler]
    y = df["target"]

    model = RandomForestClassifier(n_estimators=300, max_depth=7, random_state=42)
    model.fit(X, y)

    indikator_onemi = pd.DataFrame({'İndikatör': ozellikler, 'Etki Oranı (%)': model.feature_importances_ * 100})
    indikator_onemi = indikator_onemi.sort_values(by='Etki Oranı (%)', ascending=False).reset_index(drop=True)

    print("="*50)
    print(f"📊 {sembol} - YAPAY ZEKA İÇİN EN ÖNEMLİ İNDİKATÖRLER")
    print("="*50)
    for index, row in indikator_onemi.head(15).iterrows():
        print(f"{index + 1}. {row['İndikatör']:<20} : %{row['Etki Oranı (%)']:.2f}")

test_edilecekler = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
for sembol in test_edilecekler:
    beyni_oku(sembol)