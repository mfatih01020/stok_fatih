# QR Stok Yönetim Sistemi - Kurulum Rehberi

## Başka Bir Bilgisayara Taşıma

### Adım 1 – Klasörü Kopyala
`asım iş` klasörünün tamamını (içindeki her şeyiyle birlikte) diğer bilgisayara kopyalayın.
Klasörün içinde şunlar olmalı:
- `app.py`
- `Calistir.bat`  ← çalıştıracağınız dosya
- `requirements.txt`
- `templates/` klasörü
- `static/` klasörü
- `bkst_depo_verileri.xlsx` (varsa)

---

### Adım 2 – Python Kur (sadece bir kez)
Diğer bilgisayarda Python kurulu değilse:

1. https://www.python.org/downloads/ adresine gidin
2. En güncel Python'u indirip kurun
3. **ÖNEMLİ:** Kurulum sırasında "Add Python to PATH" kutucuğunu mutlaka işaretleyin!

---

### Adım 3 – Çalıştır
`Calistir.bat` dosyasına **çift tıklayın**.

- İlk açılışta gerekli paketleri otomatik kuracak (internet gerekli)
- Sonraki açılışlarda direkt çalışacak
- Tarayıcınız otomatik açılacak

---

## Sık Sorulan Sorular

**Python bulunamadı hatası alıyorum?**
Python'un "Add to PATH" seçeneğiyle kurulduğundan emin olun. Kurulumdan sonra bilgisayarı yeniden başlatmayı deneyin.

**Tarayıcı açılmıyor?**
Bat dosyasını çalıştırdıktan sonra tarayıcınıza manuel olarak `http://localhost:5000` yazın.

**İnternet yoksa ne olur?**
Paketler daha önce kurulduysa internet olmadan da çalışır. İlk kurulum için internet gereklidir.
