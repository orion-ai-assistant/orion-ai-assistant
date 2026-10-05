# Faz 1: Güvenlik ve yetkilendirme (sadece backend)

## Bağlam (bu dosya tek başına yeterli, önceki sohbeti bilmiyorsun)

Orion: yerel AI platformu. Bileşenler: **Hub** (backend + web dashboard, sohbet/akış/SSE/dosya/auth), **Router** (OpenAI uyumlu ağ geçidi), **Flutter istemci** (Web, Android, iOS). Hub dashboard tasarımı referanstır.

## Çalışma kuralları
- Önce repoyu incele; önceki fazlar commit edilmiş olabilir, onları bozma.
- **Sadece bu fazın kapsamını yap.** "Yapılmayacak" listesindekilere dokunma, başka faz işi ekleme.
- Minimal, mevcut yapıyı kullanan kod yaz; yeni sistem icat etme.
- Ayrı branch/PR. Bitirince "Bitti kriteri" maddelerini tek tek kontrol edip sonucu raporla (geçti/kaldı).
- Emin olmadığın bir şeyi varsayma, raporda belirt.

## Neden
LAN'da bile geçerli açıklar var; arayüzle ilgisi yok. Sonraki fazlar bunun üstüne kurulacak.

## Yapılacaklar
- `admin_key` kontrolü: anahtar yapılandırılmamışken gelen `admin_key` kabul edilmeyecek.
- Varsayılan (geliştirme) JWT sırrı ile uzak/prod modda sunucu başlamayacak; yerelde açık uyarı verilecek.
- Kayıt: açık kayıt yerine **davet veya PC sahibinin onayı**. Onaysız kullanıcı giriş yapamayacak.
- Sohbet, dosya, araç ve stream erişimi sunucuda **hesap sahibine göre** doğrulanacak; başkasının ID'sini bilmek erişim vermeyecek.
- Login'de rate limit / brute-force koruması.
- CORS izin listesi; Web'de token'ın nerede saklandığını incele, riskleri raporla (değiştirme kararını rapora bırak).

## Yapılmayacak
Arayüz değişikliği, token yenileme (Faz 3), tünel/uzak erişim (Faz 5).

## Testler (otomatik yaz)
- B kullanıcısı A'nın chat/dosya/stream ID'sine istek atınca 403/404.
- `admin_key` boşken rastgele anahtarla admin işlemi yapılamıyor.
- Varsayılan sırla uzak modda açılış reddediliyor.
- Onaysız kayıt olan kullanıcı sisteme giremiyor.
- 5–10 hatalı girişten sonra rate limit devreye giriyor.

## Bitti kriteri
Yukarıdaki testlerin hepsi geçiyor; mevcut testler bozulmadı; yapılan değişikliklerin ve kalan riskin kısa raporu var.
