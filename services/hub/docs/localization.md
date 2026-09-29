# Orion Hub dil dosyaları

Backend'in web ve Flutter'a verdiği adlar `src/orion/localization/locales/<dil>.json` dosyalarındadır. Araç ve kategori kimlikleri sabittir. Görünen adın anahtarı kimlikten türetilir: `tools.<id>.name` veya `categories.<id>.name`. Yeni bir backend dili için JSON dosyası ekleyin. Eksik anahtarlar İngilizce dosyasından okunur; orada da yoksa araç tanımındaki İngilizce `name` kullanılır.

İstemci dilini `Accept-Language` HTTP başlığıyla gönderir. Her isteğin dili ayrı çözülür; sunucuda global dil durumu tutulmaz. Katalogdaki `name` kullanıcı içindir. `Tool.description` ve `ToolInput` alan açıklamaları İngilizce model şemasında kalır ve katalog API'sine eklenmez.

Web'e ait buton, ayar ve araç paneli metinleri `src/orion/api/ui/js/locales/<dil>.js` dosyalarındadır. Yeni web dili dosyası `window.orionUiLocales.<dil>` içine `languageName` ve metin anahtarlarını kaydeder; dosyayı `index.html` içindeki `i18n.js` öncesinde yükleyin. Web dil seçimini tarayıcıdan başlatır, kullanıcının seçimini yerel depoda saklar ve tüm API isteklerinde aynı dili gönderir. Flutter kendi ekran metinlerini kendi projesinde tutar ve aynı katalog API'sini kullanır.

Modelin anlayacağı İngilizce araç açıklamaları çevrilmez. Yeni araç eklerken hem `en.json` hem desteklenen dillerdeki backend dosyalarına adını ekleyin.
