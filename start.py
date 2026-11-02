import subprocess
import threading
import time
import sys
import os
import signal
import psutil # Yeni kütüphane

def check_single_instance():
    """Start scriptinin birden fazla çalışmasını engeller."""
    pid_file = "data/start.pid"
    if not os.path.exists("data"): os.makedirs("data")
    
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            
            if psutil.pid_exists(old_pid) and old_pid != os.getpid():
                proc = psutil.Process(old_pid)
                if "python" in proc.name().lower():
                    print(f"[SYSTEM] Zaten çalışan bir BorsaAI kontrolcü bulundu (PID: {old_pid}).")
                    print("Eski süreçler temizleniyor...")
                    try:
                        # Çocuk süreçleri de temizle
                        for child in proc.children(recursive=True):
                            child.kill()
                        proc.terminate()
                    except:
                        pass
                    time.sleep(2)
        except Exception:
            pass

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

def run_bot():
    """Trading botu ana döngüsünü başlatır."""
    print("[SYSTEM] BorsaAI Trading Bot başlatılıyor...")
    try:
        subprocess.run([sys.executable, "main.py"], check=True)
    except Exception as e:
        print(f"[ERROR] Bot durduruldu: {e}")

def run_dashboard():
    """Streamlit Dashboard terminalini başlatır."""
    print("[SYSTEM] BorsaAI Elite Dashboard hazırlanıyor...")
    # Streamlit'i subprocess ile çalıştır
    try:
        # --server.port 8501 eklenebilir eğer sabit port istenirse
        subprocess.run(["streamlit", "run", "dashboard.py", "--server.headless", "true"], check=True)
    except Exception as e:
        print(f"[ERROR] Dashboard başlatılamadı: {e}")

def run_api():
    """BorsaAI FastAPI sunucusunu başlatır."""
    print("[SYSTEM] BorsaAI FastAPI Servisi başlatılıyor (port 8000)...")
    try:
        import uvicorn
        uvicorn.run("api:app", host="0.0.0.0", port=8000, log_level="warning")
    except Exception as e:
        print(f"[ERROR] FastAPI başlatılamadı: {e}")

def nuke_old_instances():
    """Borsaai ile ilgili tüm eski python süreçlerini zorla kapatır."""
    my_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Kendi sürecimizi atla
            if proc.info['pid'] == my_pid: continue
            
            # Python süreci mi?
            if "python" in (proc.info['name'] or "").lower():
                cmdline = " ".join(proc.info['cmdline'] or [])
                # Borsaai klasöründe mi veya ilgili scriptler mi?
                if "main.py" in cmdline or "dashboard.py" in cmdline or "start.py" in cmdline:
                    print(f"[CLEANUP] Eski süreç sonlandırılıyor: PID {proc.info['pid']}")
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

if __name__ == "__main__":
    # 0. Temizlik ve Tek Örnek Kontrolü
    nuke_old_instances()
    check_single_instance()
    
    # Konsol başlığını ayarla (Sadece Windows)
    if os.name == 'nt':
        os.system("title BorsaAI PRO - Elite Terminal")

    # Log ve veri klasörlerini kontrol et
    if not os.path.exists("data"):
        os.makedirs("data")
        print("[SYSTEM] Veri klasörü oluşturuldu.")
        
    print("\n" + "="*50)
    print("BORSA AI PRO - ELITE TRADING SYSTEM v2.1")
    print("="*50 + "\n")

    # Botu ayrı bir thread'de başlat
    bot_thread = threading.Thread(target=run_bot, name="BotThread", daemon=True)
    bot_thread.start()
    
    # API'yi ayrı bir thread'de başlat
    api_thread = threading.Thread(target=run_api, name="ApiThread", daemon=True)
    api_thread.start()
    
    # Veritabanı ve modellerin yüklenmesi için kısa bir süre bekle
    print("[SYSTEM] Motorlar ısıtılıyor...")
    time.sleep(3)
    
    # Dashboard'u ana thread'de başlat
    try:
        print("[SYSTEM] Dashboard erişime açılıyor: http://localhost:8501")
        run_dashboard()
    except KeyboardInterrupt:
        print("\n" + "!"*50)
        print("[EXIT] BorsaAI durduruluyor. Tüm sistemler kapatıldı.")
        print("!"*50 + "\n")
        sys.exit(0)
