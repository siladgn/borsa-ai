# Temel alınacak Python sürümü (Hafif ve hızlı olması için slim versiyon)
FROM python:3.10-slim

# Terminal loglarını anında görebilmek için
ENV PYTHONUNBUFFERED=1

# Konteyner içindeki çalışma klasörümüz
WORKDIR /app

# Önce sadece gereksinim listesini kopyala (Önbelleği verimli kullanmak için)
COPY requirements.txt .

# Kütüphaneleri kur (torch ve transformers biraz büyük olduğu için zaman alabilir)
RUN pip install --default-timeout=1000 --no-cache-dir -r requirements.txt

# Projedeki tüm kodları konteynerin içine kopyala
COPY . .

# Konteyner ayağa kalktığında çalıştırılacak komut
CMD ["python", "start.py"]