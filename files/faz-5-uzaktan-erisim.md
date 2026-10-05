# Faz 5: Uzaktan erişim (araştırma + deneme, ürün kodu yok)

## Bağlam (bu dosya tek başına yeterli, önceki sohbeti bilmiyorsun)

Orion: yerel AI platformu. Bileşenler: **Hub** (backend + web dashboard, sohbet/akış/SSE/dosya/auth), **Router** (OpenAI uyumlu ağ geçidi), **Flutter istemci** (Web, Android, iOS). Hub dashboard tasarımı referanstır.

## Çalışma kuralları
- Önce repoyu incele; önceki fazlar commit edilmiş olabilir, onları bozma.
- **Sadece bu fazın kapsamını yap.** "Yapılmayacak" listesindekilere dokunma, başka faz işi ekleme.
- Minimal, mevcut yapıyı kullanan kod yaz; yeni sistem icat etme.
- Ayrı branch/PR. Bitirince "Bitti kriteri" maddelerini tek tek kontrol edip sonucu raporla (geçti/kaldı).
- Emin olmadığın bir şeyi varsayma, raporda belirt.

## Kapsam
Telefonun mobil veriden PC'deki Orion'a erişmesi. Bu faz **karşılaştırma ve karar notu** üretir; ürün koduna eşleştirme/tünel entegrasyonu eklenmez. **Önkoşul: Faz 1 ve Faz 3 bitmiş olmalı.** Başlamadan önce Faz 1 ve 3 testlerinin geçtiğini doğrula; geçmiyorsa dur ve bildir.

## Yapılacaklar
1. Kendi cihazlarla **Tailscale** denemesi: telefon mobil veriden Hub'a ulaşıyor mu (MagicDNS adı, erişim kuralları).
2. Gerçek bağlantıda test: giriş, SSE sohbet akışı (buffering/kopma), ses WebSocket'i, 20 MB dosya yükleme.
3. PC kapalı/uykudayken uygulamanın ne göstereceği; Wake-on-LAN kararı.
4. Başkalarına dağıtım için **kalıcı tünel + eşleştirme** tasarımı (ör. Cloudflare Tunnel): QR'da ne var (tek kullanımlık kod), kalıcı cihaz kimliği nasıl doğrulanır, yetki nasıl iptal edilir, birden fazla PC.
5. Telefon yalnızca Hub'a erişir; Router ve yönetim servisleri dışarı açılmaz. Tünel seçilirse HTTP'nin yanında SSE ve WebSocket erişim kontrolünün de doğrulandığını tasarımda göster (tünel açmak tek başına erişim kontrolü değildir).
6. mDNS (yerel keşif) kısa değerlendirme: sadece aynı ağ içindir, kimlik doğrulama sağlamaz.

## Yapılmayacak
Uygulamaya eşleştirme/QR kodu yazmak, internete kalıcı olarak servis açmak, arayüz değişikliği.

## Bitti kriteri
- Mobil veriden giriş, sohbet akışı ve dosya yükleme çalışıyor (test notlarıyla).
- Yazılı çıktı: seçenek karşılaştırması (Tailscale / tünel+eşleştirme / mDNS), önerilen yön, eşleştirme akışı taslağı, riskler ve sonraki adımlar.
