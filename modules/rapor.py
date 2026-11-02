import pandas as pd
import numpy as np
import os


def backtest_raporu(csv_path="data/sinyaller.csv"):
    if not os.path.exists(csv_path):
        print("Henüz sinyal kaydı yok.")
        return None

    df = pd.read_csv(csv_path, names=[
        "tarih", "hisse", "fiyat", "rsi",
        "haber_skoru", "guven_skoru", "sonuc"
    ])

    if df.empty:
        print("Sinyal dosyası boş.")
        return None

    print(f"\n{'='*50}")
    print(f"  BORSAAI BACKTEST RAPORU")
    print(f"{'='*50}")
    print(f"Toplam sinyal  : {len(df)}")
    print(f"Tarih aralığı  : {df['tarih'].min()} → {df['tarih'].max()}")

    # Sembol bazında özet
    print(f"\n{'Sembol':<12} {'Sinyal':>7} {'Ort. Güven':>11} {'Ort. Haber':>11}")
    print("-" * 45)
    for hisse, grup in df.groupby("hisse"):
        print(
            f"{hisse:<12} {len(grup):>7} "
            f"{grup['guven_skoru'].mean():>10.1f}% "
            f"{grup['haber_skoru'].mean():>10.2f}"
        )

    # Karar dağılımı
    print(f"\nKarar dağılımı:")
    for karar, sayi in df["sonuc"].value_counts().items():
        oran = sayi / len(df) * 100
        print(f"  {karar:<35} {sayi:>4} ({oran:.1f}%)")

    # Güven skoru dağılımı
    print(f"\nGüven skoru istatistikleri:")
    print(f"  Ortalama : %{df['guven_skoru'].mean():.1f}")
    print(f"  Medyan   : %{df['guven_skoru'].median():.1f}")
    print(f"  Min/Max  : %{df['guven_skoru'].min():.1f} / %{df['guven_skoru'].max():.1f}")

    # Haber skoru ile güven skoru korelasyonu
    if len(df) > 5:
        korel = df["guven_skoru"].corr(df["haber_skoru"])
        print(f"\nHaber↔Güven korelasyonu: {korel:.3f}")
        if korel > 0.3:
            print("  → Haberler ve teknik analiz uyumlu çalışıyor.")
        elif korel < -0.3:
            print("  → Haberler ve teknik analiz ters yönde — veto sistemi devrede.")

    print(f"{'='*50}\n")
    return df


if __name__ == "__main__":
    backtest_raporu()
