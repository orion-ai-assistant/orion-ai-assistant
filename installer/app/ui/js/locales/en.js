window.orionLocales = window.orionLocales || {};
window.orionLocales.en = {
    // App states
    status_uninstalled: "Not Installed",
    status_running: "Running",
    status_stopped: "Stopped",
    status_disabled: "Disabled",
    status_starting: "Starting...",
    status_active: "Active",
    status_unavailable: "Unavailable",

    // Actions
    btn_install: "Install",
    btn_disable: "Disable",
    btn_enable: "Enable",
    btn_reinstall: "Reinstall",
    btn_remove: "Remove",
    btn_wipe_data: "Wipe Data",
    btn_delete_image: "Delete Image",
    btn_finish: "Finish",
    btn_cancel: "Cancel",

    // Labels
    lbl_hardware: "Hardware / Driver",
    ui_mode: "Mode",
    lbl_model: "Model",
    lbl_select_model: "Select Model...",
    lbl_other_actions: "Other Actions",
    lbl_unknown: "Unknown",
    lbl_scanning: "Scanning models...",
    lbl_linux_only: "{0} is only supported on Linux",
    lbl_linux_only_suffix: " (Linux only)",
    lbl_default_gpu: "Default GPU",

    // Modals & Confirmations
    confirm_delete_title: "Delete Confirmation",
    confirm_delete_msg: "Are you sure you want to delete this item?",
    confirm_delete_model_title: "Delete Model",
    confirm_delete_model_msg: "Are you sure you want to delete this model? This action cannot be undone.",
    confirm_delete_image_title: "Delete Image",
    confirm_delete_image_msg_named: 'Are you sure you want to delete the Docker image for "{0}"?',
    confirm_delete_image_msg: "Are you sure you want to delete the Docker image for this service?",
    confirm_remove_service_title: "Remove Service",
    confirm_remove_service_msg: "Are you sure you want to remove the '{0}' service? WARNING: All your data and settings will be DELETED!",
    confirm_reinstall_service_title: "Reinstall",
    confirm_reinstall_service_msg: "Are you sure you want to reinstall the '{0}' service? (Your data and settings will be saved)",
    confirm_wipe_data_title: "Wipe All Data",
    confirm_wipe_data_msg: "WARNING: Are you sure you want to wipe all data for '{0}'? This will permanently delete your chat history, database, and configurations. This action cannot be undone!",
    confirm_btn_yes: "Yes",
    confirm_btn_no: "Cancel",

    // Toasts / Messages
    msg_install_failed: "Failed to install {0}!",
    msg_service_started: "{0} started!",
    msg_service_installed: "{0} installed!",
    msg_error: "Error!",
    msg_system_starting: "Starting system...",
    msg_system_start_error: "An error occurred while starting the system!",
    msg_core_req: "Orion Router and Orion Hub services must be installed to start the system!",
    msg_not_found: "Not found",
    msg_download_started: "Download started",
    msg_conn_error: "Connection error!",
    msg_image_deleted: "Image deleted",
    msg_image_delete_failed: "Failed to delete image",
    msg_service_deleted: "Service deleted",
    msg_service_delete_failed: "Failed to delete service",
    msg_delete_endpoint_not_ready: "Delete endpoint is not ready yet.",
    msg_container_not_found_installing: "Container not found. Starting installation directly.",
    msg_reinstall_failed: "Reinstallation failed.",

    // Main UI texts
    ui_title: "Orion AI Installer",
    ui_subtitle: "Infrastructure and Service Management",
    ui_step_system: "System Requirements",
    ui_step_core: "Core Services",
    ui_step_ai: "AI Models (LLM / TTS)",
    ui_wizard_title: "System Requirements",
    ui_wizard_subtitle: "Installation of necessary tools and components.",
    ui_btn_next: "Next",
    ui_btn_back: "Back",
    ui_btn_close: "Close",
    ui_hw_os: "Operating System",
    ui_hw_cpu: "CPU",
    ui_hw_gpu: "GPU",
    ui_hw_ram: "System RAM",
    ui_hw_vram: "VRAM",

    // Wizard Steps
    ui_step_1_title: "Installation Environment",
    ui_step_1_sub: "Select Docker or Local installation.",
    ui_step_2_title: "Model Download",
    ui_step_2_sub: "Download and prepare service models.",
    ui_step_3_title: "Service Installation",
    ui_step_3_sub: "",
    ui_step_4_title: "Orion Core",
    ui_step_4_sub: "",

    // Environment Selection
    ui_env_title: "Please select the environment you want to install:",
    ui_env_docker_badge: "(Recommended)",
    ui_env_selected: "Selected",
    ui_env_docker_desc: "Installs all services in independent and isolated Docker containers. It is the stable installation method with the lowest risk of conflict, recommended by the Orion team.",
    ui_env_local: "Local Installation",
    ui_env_coming_soon: "Coming Soon",
    ui_env_local_desc: "Installs services directly onto the operating system (Native). This installation option is currently in development and will be activated soon.",

    // Models
    ui_system_models: "Preconfigured Models",
    ui_local_models: "User Added",
    ui_downloading: "Downloading",
    ui_incomplete: "Incomplete",
    ui_resume: "Resume",
    ui_download: "Download",
    ui_btn_vision: "Enable Vision and Audio",
    ui_detected: "Detected",

    // Completion
    ui_completion_title: "Installation Status",
    ui_completion_subtitle: "Active service and container statuses",
    ui_summary_total: "Total Services: {0}",
    ui_summary_active: "Active: {0}",
    ui_summary_installed: "Installed: {0}",
    param_true: "Enabled",
    param_false: "Disabled",

    // Settings
    settings_lang: "Language",
    settings_theme: "Theme",

    // Dynamic Service Names
    name_orion_router: "Orion Router",
    name_orion_hub: "Orion Hub",
    name_llama_cpp: "llama.cpp LLM Server",
    name_orion_tts: "Orion TTS",
    name_llama_cpp_embed: "llama.cpp Embedding Server",
    name_whisper_stt: "Faster Whisper STT",

    // Dynamic Service Descriptions
    desc_orion_router: "Routing service. If you change host/port values, you must also make this change for the hub.",
    desc_orion_hub: "Orion's main orchestration layer. Contains API, Worker, Redis and Database services.",
    desc_llama_cpp: "High-performance GGUF format LLM inference server. OpenAI-compatible API.",
    desc_orion_tts: "TTS service dynamically configured according to the selected model.",
    desc_llama_cpp_embed: "High-performance GGUF format multimodal embedding server.",
    desc_whisper_stt: "Fast and efficient Speech-to-Text server. OpenAI-compatible API support.",

    // Dynamic Environment Names
    env_name_llm_nv_cuda: "NVIDIA - CUDA (Fast)",
    env_name_llm_amd_rocm: "AMD - ROCm (Linux)",
    env_name_llm_vulkan: "Vulkan - All GPUs (AMD/Intel/NVIDIA)",
    env_name_llm_cpu_only: "CPU Only (Slow)",
    env_name_tts_nv_cuda: "NVIDIA - CUDA",
    env_name_tts_amd_rocm: "AMD - ROCm",
    env_name_tts_cpu: "CPU Only",
    env_name_stt_nv_cuda: "NVIDIA - CUDA",
    env_name_stt_cpu: "CPU Only",
    env_name_default: "Standard (CPU)",

    // Dynamic Parameter Labels
    param_gpu_device_ids: "GPUs to Use",
    param_gpu_layers: "GPU Layer Count",
    param_context_size: "Context Size",
    param_batch_size: "Batch Size",
    param_n_parallel: "Parallel Request Count",
    param_low_vram: "Low VRAM Mode (CPU)",
    param_idle_cleanup_mins: "Idle VRAM Cleanup (0=Disabled)",
    param_whisper_model: "Whisper Model",
    param_whisper_compute_type: "Compute Type"
};
