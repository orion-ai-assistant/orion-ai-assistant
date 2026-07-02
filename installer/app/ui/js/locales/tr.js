window.orionLocales = window.orionLocales || {};
window.orionLocales.tr = {
    // App states
    status_uninstalled: "Kurulmamış",
    status_running: "Çalışıyor",
    status_stopped: "Durduruldu",
    status_disabled: "Devre Dışı",
    status_starting: "Başlatılıyor...",
    status_preparing: "Hazırlanıyor...",
    status_active: "Aktif",
    status_unavailable: "Kullanılamaz",

    // Actions
    btn_install: "Kur",
    btn_disable: "Devre Dışı Bırak",
    btn_enable: "Aktifleştir",
    btn_reinstall: "Yeniden Kur",
    btn_remove: "Kaldır",
    btn_delete_image: "İmajı Sil",
    btn_finish: "Bitir",
    btn_cancel: "İptal Et",

    // Labels
    lbl_hardware: "Donanım / Sürücü",
    ui_mode: "Kurulum Modu",
    lbl_model: "Model",
    lbl_select_model: "Model Seçin...",
    lbl_other_actions: "Diğer işlemler",
    lbl_unknown: "Bilinmiyor",
    lbl_scanning: "Modeller taranıyor...",
    lbl_linux_only: "{0} yalnızca Linux'ta desteklenir",
    lbl_linux_only_suffix: " (Sadece Linux)",
    lbl_default_gpu: "Varsayılan GPU",

    // Modals & Confirmations
    confirm_delete_title: "Silme Onayı",
    confirm_delete_msg: "Bu öğeyi silmek istediğinize emin misiniz?",
    confirm_delete_model_title: "Modeli Sil",
    confirm_delete_model_msg: "Bu modeli silmek istediğinize emin misiniz? Bu işlem geri alınamaz.",
    confirm_delete_image_title: "İmajı Sil",
    confirm_delete_image_msg_named: '"{0}" servisinin Docker imajını silmek istediğinize emin misiniz?',
    confirm_delete_image_msg: "Bu servisin Docker imajını silmek istediğinize emin misiniz?",
    confirm_btn_yes: "Evet",
    confirm_btn_no: "İptal",

    // Toasts / Messages
    msg_install_failed: "{0} kurulumu başarısız!",
    msg_service_started: "{0} başladı!",
    msg_error: "Hata!",
    msg_system_starting: "Sistem başlatılıyor...",
    msg_system_start_error: "Sistem başlatılırken hata oluştu!",
    msg_core_req: "Sistemi başlatabilmek için Orion Router ve Orion Hub servislerinin kurulu olması gerekiyor!",
    msg_not_found: "Bulunamadı",
    msg_download_started: "İndirme başlatıldı",
    msg_conn_error: "Bağlantı hatası!",
    msg_image_deleted: "İmaj silindi",
    msg_image_delete_failed: "İmaj silinemedi",
    msg_service_deleted: "Servis silindi",
    msg_service_delete_failed: "Silme işlemi başarısız",
    msg_delete_endpoint_not_ready: "Silme uç noktası henüz hazır değil.",
    msg_container_not_found_installing: "Konteyner bulunamadı. Doğrudan kurulum başlatılıyor.",
    msg_reinstall_failed: "Yeniden kurulum başarısız.",

    // Main UI texts
    ui_title: "Orion AI Yükleyici",
    ui_subtitle: "Altyapı ve Servis Yönetimi",
    ui_step_system: "Sistem Önkoşulları",
    ui_step_core: "Çekirdek Servisler",
    ui_step_ai: "Yapay Zeka (LLM / TTS)",
    ui_wizard_title: "Sistem Önkoşulları",
    ui_wizard_subtitle: "Gerekli araçların ve bileşenlerin kurulumu.",
    ui_btn_next: "Sonraki",
    ui_btn_back: "Geri",
    ui_btn_close: "Kapat",
        ui_hw_os: "İşletim Sistemi",
    ui_hw_cpu: "İşlemci",
    ui_hw_gpu: "GPU",
    ui_hw_ram: "Sistem Belleği",
    ui_hw_vram: "VRAM Kapasitesi",
    
    // Wizard Steps
    ui_step_1_title: "Kurulum Ortamı",
    ui_step_1_sub: "Docker veya Local kurulum seçimi yapın.",
    ui_step_2_title: "Model İndirme",
    ui_step_2_sub: "Servis modellerini indir ve hazırla.",
    ui_step_3_title: "Servis Kurulumu",
    ui_step_3_sub: "",
    ui_step_4_title: "Orion Core",
    ui_step_4_sub: "",
    
    // Environment Selection
    ui_env_title: "Lütfen kurulum yapmak istediğiniz ortamı seçin:",
    ui_env_docker_badge: "(Önerilen)",
    ui_env_selected: "Seçili",
    ui_env_docker_desc: "Tüm servisleri bağımsız ve izole Docker konteynerleri içerisinde kurar. Çakışma riski en düşük, Orion ekibi tarafından önerilen kararlı kurulum yöntemidir.",
    ui_env_local: "Local Kurulum",
    ui_env_coming_soon: "Yakında",
    ui_env_local_desc: "Servisleri doğrudan işletim sistemi üzerine kurar (Native). Bu kurulum seçeneği şu anda geliştirme aşamasındadır, yakında aktif edilecektir.",

    // Models
    ui_system_models: "Hazır Modeller",
    ui_local_models: "Sizin Ekledikleriniz",
    ui_downloading: "İndiriliyor",
    ui_incomplete: "Yarıda Kaldı",
    ui_resume: "Devam Et",
    ui_download: "İndir",
    ui_btn_vision: "Vision ve Audio Aktifleştir",
    ui_detected: "Algılanan",

    // Completion
    ui_completion_title: "Kurulum Durumu",
    ui_completion_subtitle: "Aktif servis ve container durumları",
    ui_summary_total: "Toplam Servis: {0}",
    ui_summary_active: "Aktif: {0}",
    ui_summary_installed: "Kurulu: {0}",
    param_true: "Aktif",
    param_false: "Kapalı",
    
    // Settings
    settings_lang: "Dil",
    settings_theme: "Tema",

    // Dynamic Service Names
    name_orion_router: "Orion Router",
    name_orion_hub: "Orion Hub",
    name_llama_cpp: "llama.cpp LLM Sunucusu",
    name_orion_tts: "Orion TTS",
    name_llama_cpp_embed: "llama.cpp Embedding Sunucusu",
    name_whisper_stt: "Faster Whisper STT",

    // Dynamic Service Descriptions
    desc_orion_router: "Yönlendirme servisidir. Eğer host/port değerlerini değiştirirseniz, hub için de bu değişikliği yapmanız gerekir.",
    desc_orion_hub: "Orion'un ana orkestrasyon katmanı. API, Worker, Redis ve Veritabanı servislerini içerir.",
    desc_llama_cpp: "Yüksek performanslı, GGUF formatında LLM inference sunucusu. OpenAI-compatible API.",
    desc_orion_tts: "Seçilen modele göre dinamik olarak yapılandırılan TTS servisi.",
    desc_llama_cpp_embed: "Yüksek performanslı, GGUF formatında multimodal embedding sunucusu.",
    desc_whisper_stt: "Hızlı ve verimli Speech-to-Text sunucusu. OpenAI-compatible API desteği.",

    // Dynamic Environment Names
    env_name_llm_nv_cuda: "NVIDIA - CUDA (Hızlı)",
    env_name_llm_amd_rocm: "AMD - ROCm (Linux)",
    env_name_llm_vulkan: "Vulkan - Tüm GPU'lar (AMD/Intel/NVIDIA)",
    env_name_llm_cpu_only: "Sadece CPU (Yavaş)",
    env_name_tts_nv_cuda: "NVIDIA - CUDA",
    env_name_tts_amd_rocm: "AMD - ROCm",
    env_name_tts_cpu: "Sadece CPU",
    env_name_stt_nv_cuda: "NVIDIA - CUDA",
    env_name_stt_cpu: "Sadece CPU",
    env_name_default: "Standart (CPU)",

    // Dynamic Parameter Labels
    param_gpu_device_ids: "Kullanılacak GPU'lar",
    param_gpu_layers: "GPU Katman Sayısı",
    param_context_size: "Bağlam Boyutu (Context)",
    param_batch_size: "Batch Boyutu",
    param_n_parallel: "Paralel İstek Sayısı",
    param_low_vram: "VRAM Tasarrufu (CPU)",
    param_idle_cleanup_mins: "Boşta VRAM Temizleme (0=Kapalı)",
    param_whisper_model: "Whisper Modeli",
    param_whisper_compute_type: "Hesaplama Tipi (Compute)"
};
