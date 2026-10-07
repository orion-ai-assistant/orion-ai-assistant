# Hub test modernizasyonu sonucu — 7 Ekim 2026

Plan: `hub-test-modernization-plan-2026-10-07.md`. Çalışma Hub checkout'unda yapıldı; Router/Flutter ve tasarım kaynakları değiştirilmedi. Commit/push yapılmadı. `.tmp/policy-test-runtime` Python 3.11 ortamı kullanıldı; Hub `.venv` değiştirilmedi.

## Kaynak düzeltmeleri

Yalnız `services/hub/src/orion/api/router_connection.py` değişti:

- `refresh()` bozuk seçim kaydını artık yokmuş gibi değerlendirmiyor. Kaydı bootstrap/resolver'dan önce okuyor; bozuk dosyaya otomatik seçim yazmıyor. 0/1/2 trusted kayıt varyantları regresyon testinde doğrulandı.
- Kayıtlı pin ile UUID'ye ait güven kaydının pini farklıysa refresh hata veriyor. Önceden status bu durumda kimliği doğrulanmış gösterebiliyordu; worker adres çözümlemesi zaten bunu reddediyordu.
- `status()` bozuk kayıt veya pin uyuşmazlığını güvenli 503 yanıtına çeviriyor. Owner scope kontrolü hata dönüştürme sınırından önce kalıyor.
- Router rotası mevcut `CredentialRoute` doğrulama filtresini kullanıyor. Yeni negatif ASGI testi önce 422 yanıtında URL parolası/token sızıntısını gösterdi; düzeltmeden sonra yanıt yalnız loc/msg/type içeriyor. TLS/pin doğrulaması gevşetilmedi.

Owner login 401 kaynak hatası değildi: `security.valid_admin_key()` diskteki owner-password.hash kaydına ortam değişkeninden öncelik veriyor; fingerprint aynı kaynağı kullanıyor. Geçici security diziniyle gerçek credential kontrolü çalıştırıldı. Yanlış parola, anonim erişim, people scope, admin scope, kötü Origin ve uzak istemci sınırları korundu.

Offline davranışı eski TTL beklentisine uyarlanmadı: resolver adres bulamayınca güvenilir kaydın önceki adresi kalabilir. Kayıtlı UUID/pin ve seçim korunuyor, başka trusted Router'a veya env adresine otomatik geçilmiyor. Gerçek pinned transport ile kapalı localhost portuna istek başarısızlığı sınandı. Eski `selected_router_url()` 75 saniye TTL'i güncel güven yolunun sözleşmesi değil.

## Eski 21 testin yeni karşılıkları

Aşağıdaki adlarda `test_` öneki vardır. Dosya yolları `services/hub/tests/` altındadır.

### test_router_connection.py: 11 → 16

| Eski ad | Yeni karşılık |
| --- | --- |
| single_router_is_checked_and_uuid_persists_for_worker | single_trusted_router_persists_uuid_and_pin_for_worker |
| multiple_routers_are_not_automatically_selected | multiple_trusted_routers_are_not_automatically_selected |
| existing_unique_hostname_migrates_to_its_same_uuid | discovery_and_legacy_hostname_do_not_create_trust; saved_router_updates_address_but_preserves_uuid_and_pin |
| failed_api_check_does_not_create_an_association | failed_identity_verification_never_changes_trust_or_selection; verified_identity_can_exist_without_account_access |
| saved_router_updates_ip_but_never_changes_uuid | saved_router_updates_address_but_preserves_uuid_and_pin |
| offline_saved_router_and_expired_endpoint_fail_closed | offline_saved_router_does_not_switch_to_other_trust_or_env; revoked_selection_is_not_replaced_by_single_remaining_router |
| conflicting_uuid_is_not_selected | conflicting_discovery_uuid_is_rejected_before_verification |
| explicit_environment_and_manual_selection_are_preserved | saved_identity_is_preserved_when_environment_changes; manual_selection_requires_uuid_and_verified_pin |
| corrupt_selection_is_not_deleted_or_replaced | aynı ad (0/1/2 trusted); corrupt_selection_status_returns_safe_error_without_bootstrap |
| owner_selection_requires_admin_scope_and_mount_stays_local | aynı ad; negatif URL yanıtı, uzak istemci ve people POST sınırları da doğrulanıyor |
| real_identity_and_api_key_checks_happen_in_order | real_tls_pin_and_uuid_are_checked_before_account_auth |

Ek regresyon: `refresh_rejects_saved_pin_mismatch`. Hostname/IP güncellemesi kimlik üretmez. Discovery güven oluşturmaz; manuel seçim UUID ve doğrulanmış pin ister. Yanlış UUID/pin doğrulamasında trust ve seçim yazımı sıfırdır, önceki seçim korunur.

### test_router_address.py: 5 → 10

| Eski ad | Yeni karşılık |
| --- | --- |
| explicit_hostname_url_is_used_without_ip_configuration | trusted_hostname_is_used_without_ip_configuration |
| explicit_url_overrides_legacy_host_and_port | selection_overrides_legacy_environment |
| existing_native_and_docker_defaults_remain_usable_before_mdns_setup | native_and_docker_https_defaults_and_tls_port |
| custom_hostnames_are_already_supported_by_legacy_configuration | custom_hostname_fallback_does_not_create_trust |
| bad_or_sensitive_urls_are_rejected_without_printing_credentials | bad_urls_are_rejected_with_safe_error_messages; owner ASGI testi gerçek 422 yanıtında sırları da kontrol ediyor |

Ek regresyonlar: localhost/v1 normalizasyonu, seçilmiş UUID'nin trust'ta olmaması, pin uyuşmazlığı, seçim olmadan tek trusted Router, birden fazla trusted Router için açık seçim gerekliliği.

### test_catalog_partial.py: 5 → 6

Beş eski ad korundu: `unreachable_local_tts_does_not_block_other_catalogs`, `outage_preserves_previous_voices_and_recovers`, `cold_model_save_never_requests_voice_services`, `poll_and_first_save_share_model_request`, `validation_finishes_while_voice_refresh_is_pending`.

Session mock'u `router_session()` sınırına, credential mock'u `router_accounts.catalog_credentials()` sınırına taşındı. HTTPS origin, katalog prefix'i ve fixture auth header'ı doğrulanıyor. Timestamps, generation, cache'ler ve task state izole; task'lar cancel/await ile temizleniyor. Concurrent poll/save için model isteği event ile durduruluyor; ikisi beklerken tek in-flight refresh paylaşımı sınanıyor. Voice event beklemeleri timeout'lu.

Yeni test: `cache_clear_prevents_old_inflight_catalog_from_being_published`; bağlantı değişirken cache generation değişimi eski isteğin sonucunu yayımlamasını engelliyor.

## İlgili fixture düzeltmeleri

- `test_router_selection_security.py`: local_router_identity ve katalog cache temizleme sınırları mock'landı; makinedeki kurulumun veya global cache'in sonucu etkilemesi engellendi.
- `test_router_accounts.py`: hesap kataloğu çağrıları arasında izinli liste boşaltılıyor; sonraki cevap eski izni kullanmıyor. Her çağrı hesap credential'ını kullanıyor; admin credential yok.
- `test_owner_invitations.py`: uzak kayıt testinin istemcisi uzak IP olarak kuruldu; geçici security/connection/LOCALAPPDATA, mock provision ve beklenmeyen registry DB erişimini reddeden fixture eklendi. Panel ana bağlantısı güncel `/panel/` beklentisine taşındı. Davetsiz uzak kayıt hâlâ 403, rate limit 429; davetli kayıt testi korunuyor.

## Doğrulama

Birleşik koşu: modernize edilen üç dosya + `test_router_accounts.py`, `test_router_provider_auth.py`, `test_router_selection_security.py`, `test_router_api_key_masking.py`, `test_router_bootstrap.py`, `test_model_defaults.py`, `test_local_owner_onboarding.py`, `test_owner_invitations.py`, `test_security.py`.

Sonuç: **124 passed, 0 failed, 0 skipped/xfail; 22 subtests passed** (51.91 saniye). İki mevcut dependency deprecation uyarısı var: aiohttp ClientSession kalıtımı ve Starlette/httpx TestClient. Son koşu geçici security/connection/LOCALAPPDATA/TEMP/TMP ile yapıldı; `asyncpg.connect` beklenmeyen kullanımda hata verecek mock ile korundu ve bağlantı girişimi **0** idi.

Üç modernize dosyada toplam **32 test** var (önceden 21). Bu 32 testin her biri ayrı Python sürecinde tek tek de geçti: **32 passed, 0 failed, 0 skipped**. Her süreçte aynı geçici dizin izolasyonu ve PostgreSQL bağlantı kontrolü kullanıldı.

Gerçek TLS integration testi çalıştı: geçici sertifikadan SPKI, doğru kimlik, yanlış UUID, yanlış pin ve yanlış pin altında Authorization içeren transport isteği sınandı. Pin uyuşmazlığında handler çağrı sayısı artmadı. Bootstrap regresyonları da gerçek yerel fixture TLS sunucularıyla geçti. Katalog unit mock sonuçları gerçek TLS doğrulaması olarak sayılmadı.

`git diff --check` geçti.

### İlk geniş koşunun izolasyon sınırı

İlk geniş owner regresyon koşusunda üç eski test başarısız oldu. İki davetsiz kayıt testi loopback istemci nedeniyle güncel yerel kayıt yoluna girdi ve 409 döndü; bu yol canlı ortam yapılandırmasındaki PostgreSQL bağlantısına erişebiliyordu. Bu koşu tam izolasyon kanıtı değildir. Yeni kullanıcı oluşturulduğu gözlenmedi (iki cevap da conflict), fakat bu oturumun tamamında canlı DB yoluna hiç erişilmediği iddia edilmiyor. Fixture düzeltmesi ve bağlantı kontrolü sonrasında aynı geniş set sıfır DB girişimiyle geçti. Canlı veritabanında inceleme/temizleme veya geri alma yapılmadı.

İlk sandbox koşuları Windows asyncio socketpair oluştururken takıldı; faulthandler bunu test gövdesinden önce gösterdi. Bu görevde başlatılan takılı süreçler kapatıldı. Son async/TLS koşularına sandbox dışı yürütme izni verildi; uygulama transport politikası değiştirilmedi.

Tekrar çalıştırma: bu checkout içinde `.tmp/run_hub_regressions.py` birleşik seti geçici dizin ve DB bağlantı kontrolüyle çalıştırır; `.tmp/run_hub_individual.py` 32 testi ayrı süreçlerde çalıştırır. Bu yardımcılar çalışma çıktısıdır, Git kapsamına alınmadı. Her iki komut için PYTHONPATH Hub src ve repo kökünü içermelidir.
