import os
import shutil
import sqlite3
import psutil
import time

def nuke_old_instances():
    """Borsaai ile ilgili tüm eski python süreçlerini zorla kapatır."""
    my_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == my_pid: continue
            if "python" in (proc.info['name'] or "").lower():
                cmdline = " ".join(proc.info['cmdline'] or [])
                if "main.py" in cmdline or "dashboard.py" in cmdline or "start.py" in cmdline:
                    print(f"🧨 [CLEANUP] Eski süreç sonlandırılıyor: PID {proc.info['pid']}")
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(2) # Dosyaların serbest kalması için bekle

def reset_system():
    print("🧹 [CLEANUP] BorsaAI Veri Temizleme Başlatılıyor...")
    nuke_old_instances()
    
    # 1. Veritabanını temizle
    db_path = "data/borsa_ai.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            print("✅ Veritabanı silindi.")
        except Exception as e:
            print(f"❌ Veritabanı silinemedi: {e}")

    # 2. Paper Trading durumunu temizle
    state_path = "data/paper_state.json"
    if os.path.exists(state_path):
        try:
            os.remove(state_path)
            print("✅ Paper Trading durumu sıfırlandı.")
        except Exception as e:
            print(f"❌ State dosyası silinemedi: {e}")

    # 3. Logları temizle (Eğer ayrı bir klasördeyse)
    if os.path.exists("logs"):
        try:
            shutil.rmtree("logs")
            os.makedirs("logs")
            print("✅ Sistem logları temizlendi.")
        except Exception as e:
            print(f"❌ Loglar temizlenemedi: {e}")

    # 4. Modelleri temizlemek istersen (Opsiyonel - Şimdilik kalsın ki AI hemen çalışsın)
    # model_dir = "data/models"
    # if os.path.exists(model_dir):
    #     shutil.rmtree(model_dir)
    #     os.makedirs(model_dir)
    #     print("✅ Yapay zeka modelleri sıfırlandı.")

    print("\n✨ [SUCCESS] Tüm veriler temizlendi! Sistem artık tertemiz.")
    print("🚀 Şimdi 'python start.py' komutuyla botu sıfırdan başlatabilirsiniz.")

if __name__ == "__main__":
    reset_system()
