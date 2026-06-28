# WSL2 Zaman Kayması ve Docker Load Testleri

## Sorunun Özeti

Windows üzerinde Docker çalıştırırken konteynerler doğrudan Windows üzerinde çalışmazlar. Docker, konteynerleri arka planda WSL2 (Windows Subsystem for Linux) içinde, Hyper-V tabanlı hafif bir sanal makine üzerinde çalıştırır.

Bu nedenle Windows ile Linux ortamları arasında saat ve zamanlama farklılıkları oluşabilir. Özellikle load testleri veya `sleep`/zaman ölçümleri yaptığınız kodlarda bu fark kendini gösterebilir.

## Neden Oluyor?

1. **CPU saat sayacı (TSC)** ve işletim sistemi zaman yönetimi farklı çalışır.
2. Windows, enerji tasarrufu ve CPU frekansı değişimlerini düzgün takip eder.
3. WSL2 içindeki Linux çekirdeği bu frekans değişimlerine her zaman tam senkronize olamaz.
4. Sonuçta, WSL2 içindeki Linux zamanın farklı bir hızda aktığını düşünebilir.

Örneğin:

* Docker konteynerindeki worker `sleep(1)` yaptığında, WSL2 Linux bunu kendi saatine göre "1 saniye" olarak serbest bırakır.
* Ancak gerçek dünyada Windows saatiyle bu aralık 950-960 ms olabilir.

## Ne Gözlemlenir?

* Windows tarafında çalışan test scripti, gerçek zamanı ölçerken kısa bir süre (örneğin 940 ms) raporlayabilir.
* Docker içindeki worker ise kendi saatine göre doğru olan 1 saniyeyi kullanır.
* Bu durum, özellikle `time.perf_counter()` veya zaman bazlı gecikme ölçümlerinde yanıltıcı sonuçlar verir.

## Ne Yapmalısın?

Bu durumda kodun kendisi hatalı değildir. Sorun büyük olasılıkla Windows + WSL2 arasında oluşan zaman uyumsuzluğundan kaynaklanır.

Bunun için en iyi yaklaşım:

* Load testi doğrudan Docker/WSL2 içinden çalıştırmak,
* Veya gerçek bir Linux ortamında (örneğin bir Ubuntu sunucusunda) test yapmak.

## Uygulamada Ne Değişir?

* Lokal Windows ortamında çalışan test scripti `~940 ms` gibi kısa bir değer gösterebilir.
* Docker içinden veya gerçek Linux ortamından çalıştırdığında bu değer `>1000 ms` olarak düzelenir.
* Production ortamında (native Linux) bu problem büyük ihtimalle ortadan kalkar.

## Nasıl Çalıştırılır?

Kayan kodu Windows yerine konteyner içinden test etmek için:

```bash
# Önce doğru konteyner adını bul
docker ps

# Scripti konteynere kopyala, container adı şu an "backend-worker-1" ama farklı da olabilirdi 
docker cp scripts/load_test.py backend-worker-1:/tmp/load_test.py

# Konteyner içinde çalıştır
docker exec -it -e ORION_BASE_URL=http://api:8000 backend-worker-1 python /tmp/load_test.py
```

> Burada `ORION_BASE_URL=http://api:8000` ayarı çok önemlidir. Çünkü konteyner içinden `localhost` kendi iç ağa işaret eder, API servisine erişim için konteyner DNS/hostname kullanmak gerekir.

## Sonuç

* Kodun normalde doğru.
* Sadece Windows + WSL2 sanallaştırma katmanı zaman ölçümlerini bozuyor.
* Bu durum production ortamında tekrar etmemeli.

Bu notu repo içinde saklayarak, benzer bir durumda tekrar zaman kayması tuzağına düşmemiş olursun.