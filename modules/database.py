import sqlite3
import os
import json
from datetime import datetime

DB_PATH = "data/borsa_ai.db"
os.makedirs("data", exist_ok=True)

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # İşlemler Tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS islemler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sembol TEXT,
                side TEXT,
                giris_fiyat REAL,
                miktar REAL,
                stop_loss REAL,
                take_profit REAL,
                durum TEXT DEFAULT 'ACIK', -- ACIK, KAPALI
                kar_zarar REAL DEFAULT 0.0,
                giris_zamani TEXT,
                cikis_zamani TEXT,
                ai_guven REAL,
                ek_bilgi TEXT, -- JSON string for trailing stop etc.
                neden TEXT     -- İşlem açılış gerekçesi
            )
        """)
        
        # AI Tahminleri Tablosu (Gelecekte analiz için)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tahminler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sembol TEXT,
                zaman TEXT,
                tahmin_yon TEXT,
                tahmin_olasilik REAL,
                gerceklesen_yon TEXT,
                dogruluk INTEGER -- 1: Doğru, 0: Yanlış
            )
        """)
        
        # Loglar Tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS loglar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zaman TEXT,
                seviye TEXT,
                mesaj TEXT,
                kaynak TEXT
            )
        """)
        
        # Sütun kontrolü (Eski veritabanları için)
        try:
            cursor.execute("ALTER TABLE islemler ADD COLUMN neden TEXT")
        except sqlite3.OperationalError:
            pass # Zaten varsa hata verir, görmezden gel
            
        conn.commit()

def islem_kaydet(sembol, veri):
    """Eski JSON sistemini taklit eder ama DB'ye yazar."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        # Eğer bu sembol için açık işlem varsa güncelle, yoksa ekle
        cursor.execute("SELECT id FROM islemler WHERE sembol = ? AND durum = 'ACIK'", (sembol,))
        row = cursor.fetchone()
        
        ek_bilgi = json.dumps(veri.get("ek_bilgi", {}))
        
        if row:
            cursor.execute("""
                UPDATE islemler SET 
                    giris_fiyat = ?, miktar = ?, stop_loss = ?, take_profit = ?, 
                    ai_guven = ?, ek_bilgi = ?, neden = ?
                WHERE id = ?
            """, (veri['giris_fiyat'], veri.get('miktar', 0), veri.get('stop_loss', 0), 
                  veri.get('take_profit', 0), veri.get('ai_guven', 0), ek_bilgi, 
                  veri.get('neden', ''), row[0]))
        else:
            cursor.execute("""
                INSERT INTO islemler (sembol, side, giris_fiyat, miktar, stop_loss, take_profit, giris_zamani, ai_guven, ek_bilgi, neden)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sembol, veri['side'], veri['giris_fiyat'], veri.get('miktar', 0), 
                  veri.get('stop_loss', 0), veri.get('take_profit', 0), 
                  datetime.now().isoformat(), veri.get('ai_guven', 0), ek_bilgi, 
                  veri.get('neden', '')))
        conn.commit()

def bugun_acilan_islem_sayisi() -> int:
    """Yerel tarihe göre bugün açılmış (kayıtlı) tüm işlemler — günlük minimum kota için."""
    init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM islemler
                WHERE giris_zamani IS NOT NULL
                  AND substr(giris_zamani, 1, 10) = ?
                """,
                (today,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def acik_islemleri_yukle():
    init_db()
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM islemler WHERE durum = 'ACIK'")
        rows = cursor.fetchall()
        
        res = {}
        for r in rows:
            res[r['sembol']] = dict(r)
            if r['ek_bilgi']:
                res[r['sembol']]['ek_bilgi'] = json.loads(r['ek_bilgi'])
        return res

def islem_kapat(sembol, cikis_fiyat, kar_zarar):
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE islemler SET 
                durum = 'KAPALI', cikis_zamani = ?, kar_zarar = ?
            WHERE sembol = ? AND durum = 'ACIK'
        """, (datetime.now().isoformat(), kar_zarar, sembol))
        conn.commit()

def log_ekle(mesaj, seviye="INFO", kaynak="SISTEM"):
    init_db()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO loglar (zaman, seviye, mesaj, kaynak) VALUES (?, ?, ?, ?)",
                        (datetime.now().isoformat(), seviye, mesaj, kaynak))
            conn.commit()
    except:
        pass # Log yazamazsa hata vermesin

def tahmin_kaydet(sembol, yon, olasilik):
    """AI tahminini veritabanına kaydeder."""
    init_db()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tahminler (sembol, zaman, tahmin_yon, tahmin_olasilik)
                VALUES (?, ?, ?, ?)
            """, (sembol, datetime.now().isoformat(), yon, olasilik))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Tahmin kaydetme hatası: {e}")

if __name__ == "__main__":
    init_db()
    print("✅ Veritabanı başarıyla başlatıldı.")
