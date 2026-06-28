# Artillery ile Basit Yük Testi

Bu yöntem tamamen bedava ve hızlıdır. Kod yazmana gerek yok; sadece bir YAML ayar dosyası oluşturup Artillery ile istediğin senaryoyu (bağlan, bekle, mesaj at, vb.) simüle edebilirsin.

## 1. Adım: Artillery Kurulumu
Bilgisayarında Node.js yüklü olduğunu varsayıyorum. Eğer yüklü değilse bile `npx` ile kurmadan çalıştırabilirsin.

Terminale şunu yaz:

```bash
npm install -g artillery
```

## 2. Adım: Test Senaryosunu Hazırla
Masaüstünde `test.yml` adında bir dosya oluştur ve içine şu kodları yapıştır.

```yaml
config:
  target: "http://localhost:3000" # Kendi backend adresini/portunu yaz
  phases:
    - duration: 10          # Test toplam 10 saniye sürsün
      arrivalRate: 10       # Her saniye 10 yeni kişi gelsin (10 sn sonunda 100 kişi olur)
  engines:
    socketio-v3: {}         # Socket.io sürümün 3 veya 4 ise bunu kullan

scenarios:
  - name: "Bağlan ve Mesaj At"
    engine: socketio-v3
    flow:
      - emit:
          channel: "join"    # Varsa bir odaya katılma event'i
          data: { "room": "test-room" }
      - think: 1             # 1 saniye bekle
      - emit:
          channel: "chat message" # Mesaj gönderilecek event adı
          data: "Merhaba, ben bir test botuyum!"
      - think: 2             # Bağlantıyı hemen koparmamak için biraz daha bekle
```

Bu dosya senin istediğin senaryoyu simüle eder: bir bağlantı aç, 1 saniye bekle, mesaj gönder ve bağlantıyı kısa süre sonra sonlandır.

## 3. Adım: Testi Çalıştır
Terminalde bu dosyanın olduğu dizine gel ve şu komutu çalıştır:

```bash
artillery run test.yml
```

---

## Neden Bu Yöntem "Gerçekçi"?

Artillery şu imkanları sağlar:

- **Virtual Users (VU):** Her bir bağlantıyı bağımsız sanal kullanıcı olarak yönetir.
- **Aşamalı Yükleme:** 100 kişiyi aynı anda değil, saniyelere yayarak bağlar; bu daha gerçekçi bir senaryo sunar.
- **Raporlama:** Test bitince kaç istek gittiğini, sunucunun yanıt süresini (latency) ve diğer verileri gösterir.

## Docker İle Kullanım
Eğer Docker kullanmak istersen, Artillery'yi yerel olarak kurmana gerek yoktur. Aşağıdaki komutla doğrudan Docker konteynerinden çalıştırabilirsin:

```bash
docker run --rm -it -v $(pwd):/scripts artilleryio/artillery:latest run /scripts/test.yml
```

## Bedava "Farklı Cihaz" Hilesi
Eğer tek IP'den gittiğini düşünüyorsan ve sunucunun bu şekilde tüm istekleri aynı kaynaktan saymasını engellemek istiyorsan, en basit ücretsiz çözüm şudur:

1. Test scriptini bir arkadaşının bilgisayarına gönder.
2. Kendi telefonundan mobil ağ (hotspot) aç ve bilgisayarı ona bağla.
3. Böylece sunucu, farklı bir dış IP üzerinden gelen istekleri görür.

Bu yöntemle 100-200 bağlantıyı yönetmek kolaydır. Sunucun dayanıyorsa 500-1000'e kadar da yükleme yapabilirsin.

## Not
Sunucu tarafında Socket.IO için **Redis Adapter** kullanıyor musun yoksa tüm bağlantılar tek bir instance üzerinde mi duruyor? Bu, ölçeklenebilirlik açısından fark yaratır.
