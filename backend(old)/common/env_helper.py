import os
from dotenv import load_dotenv


def get_env(key: str, required: bool = True) -> str | None:
    value = os.getenv(key)
    if required and value is None:
        raise RuntimeError(f"'{key}' environment variable is not set! Uygulamayı başlatmak için .env dosyasına ekleyin.")
    return value


# .env yüklemesi burada yapılabilir, ya da settings dosyalarında ayrı ayrı çağrılabilir
load_dotenv()  # Lokal geliştirme için .env okur; Docker'da environment: zaten set edildiğinden etkisiz

# Vertex AI için ADC path çift destek: host kök path ya da container içi yerleşik path
if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    host_cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_HOST")
    if host_cred:
        # Lokal çalıştırmada host yolu kullanılabilir.
        # Docker konteynerinde host yolu mevcut olmayabilir; bu durumda mount edilmesi beklenen konteyner path'e geç.
        if os.path.exists(host_cred):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = host_cred
        else:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/app/gcp/application_default_credentials.json"
