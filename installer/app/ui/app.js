import * as api from './js/api.js';
import * as uiRender from './js/ui-render.js';
import { showToast } from './js/ui-utils.js';

let previousServiceStates = {};
let allServiceModels = {};
let allServices = {};
let currentStep = 1;
let isSystemStarting = false;

function isCoreService(service) {
    return service?.id === 'orion-hub' || service?.category === 'core' || service?.category === 'hub' || service?.category === 'router';
}

const steps = [
    {
        id: 1,
        title: 'Kurulum Ortamı',
        subtitle: 'Docker veya Local kurulum seçimi yapın.'
    },
    {
        id: 2,
        title: 'Model Indirme',
        subtitle: 'Servis modellerini indir ve hazirla.'
    },
    {
        id: 3,
        title: 'Servis Kurulumu',
        subtitle: ''
    },
    {
        id: 4,
        title: 'Orion Core',
        subtitle: ''
    }
];

async function fetchHardware() {
    try {
        const info = await api.fetchHardware();
        
        // GPU İsmi
        const gpuNameEl = document.getElementById('hw-gpu-name');
        if (gpuNameEl) gpuNameEl.innerText = info.DETECTED_GPU_NAME || "Bilinmiyor";

        // VRAM
        const vramEl = document.getElementById('hw-vram-gb');
        if (vramEl) vramEl.innerText = info.DETECTED_VRAM_GB ? `${info.DETECTED_VRAM_GB} GB` : "0 GB";

        // CPU
        const cpuEl = document.getElementById('hw-cpu-name');
        if (cpuEl) cpuEl.innerText = info.DETECTED_CPU || "Tespit edilemedi";

        // OS Badge
        const osIcon = document.getElementById('os-icon');
        const osText = document.getElementById('os-text');
        if (osIcon && osText) {
            const os = info.OS_PLATFORM || "unknown";
            window.orionOsPlatform = os;
            osText.innerText = os.charAt(0).toUpperCase() + os.slice(1);
            if (os === 'windows') osIcon.className = 'fab fa-windows';
            else if (os === 'linux') osIcon.className = 'fab fa-linux';
            else osIcon.className = 'fas fa-desktop';
        }
        
        // GPU List
        try {
            window.orionGpuList = info.DETECTED_GPU_LIST ? JSON.parse(info.DETECTED_GPU_LIST) : [];
        } catch(e) {
            window.orionGpuList = [];
        }

    } catch (err) { 
        console.error("Hardware fetch error:", err); 
    }
}

function getServiceRuntimeState(service) {
    if (!service?.is_installed) return { label: 'Kurulmamış', className: 'status-missing' };
    if (service.autostart === false) return { label: 'Devre Dışı', className: 'status-stopped' };
    if (service.is_running) return { label: 'Çalışıyor', className: 'status-running' };
    if (isSystemStarting) return { label: 'Başlatılıyor...', className: 'status-starting' };
    return { label: 'Durduruldu', className: 'status-stopped' };
}

function renderCompletionPanel() {
    const summaryEl = document.getElementById('completion-summary');
    const listEl = document.getElementById('completion-status-list');
    if (!summaryEl || !listEl) return;

    const services = Object.values(allServices).filter(s => s.status !== 'disabled');
    const activeCount = services.filter(s => s.is_installed && s.autostart !== false).length;
    const installedCount = services.filter(s => s.is_installed).length;

    summaryEl.innerHTML = `
        <span class="summary-pill">Toplam Servis: ${services.length}</span>
        <span class="summary-pill">Aktif: ${activeCount}</span>
        <span class="summary-pill">Kurulu: ${installedCount}</span>
    `;

    listEl.innerHTML = services.map(service => {
        const state = getServiceRuntimeState(service);
        return `
            <div class="completion-status-item">
                <div>
                    <div class="completion-service-name">${service.name}</div>
                    <div class="completion-service-meta">${service.category.toUpperCase()}</div>
                </div>
                <div class="status-badge ${state.className}" title="${state.label}">
                    <span class="status-dot"></span>
                    <span>${state.label}</span>
                </div>
            </div>
        `;
    }).join('');
}

function openCompletionPanel() {
    const panel = document.getElementById('completion-panel');
    if (!panel) return;
    renderCompletionPanel();
    panel.classList.remove('hidden');
}

function closeCompletionPanel() {
    const panel = document.getElementById('completion-panel');
    if (!panel) return;
    panel.classList.add('hidden');
}

async function fetchServices() {
    try {
        const services = await api.fetchServices();
        
        services.forEach(service => {
            const prevState = previousServiceStates[service.id];
            if (prevState) {
                if (prevState.is_installing && !service.is_installing) {
                    if (service.is_installed) {
                        showToast(`${service.name} başarıyla kuruldu!`, 'success');
                    } else {
                        showToast(`${service.name} kurulumu başarısız!`, 'error');
                    }
                }

                // Track service startup transition
                if (!prevState.is_running && service.is_running) {
                    showToast(`${service.name} başladı!`, 'success');
                }
            }
            allServices[service.id] = service;
            previousServiceStates[service.id] = { is_installing: service.is_installing, is_running: service.is_running };
            
            if (service.status !== 'disabled' && !isCoreService(service)) {
                loadModelStatus(service.id);
            }
        });



        uiRender.renderServices(services, previousServiceStates, allServiceModels, {
            onStart: installService,
            onToggleAutostart: toggleAutostart,
            onReinstall: reinstallService,
            onRemove: removeService,
            onDeleteImage: deleteImage,
            onDownload: downloadModel,
            onModelChange: (serviceId, path) => uiRender.filterVisionModels(serviceId, path, allServiceModels),
            onTabModels: (serviceId) => loadModelStatus(serviceId)
        }, { step: currentStep });

        const completionPanel = document.getElementById('completion-panel');
        if (completionPanel && !completionPanel.classList.contains('hidden')) {
            renderCompletionPanel();
        }

        updateWizardUI();
    } catch (err) { console.error("Services fetch error:", err); }
}

function renderFromCache() {
    const services = Object.values(allServices);
    if (services.length === 0) return;

    uiRender.renderServices(services, previousServiceStates, allServiceModels, {
        onStart: installService,
        onToggleAutostart: toggleAutostart,
        onReinstall: reinstallService,
        onRemove: removeService,
        onDeleteImage: deleteImage,
        onDownload: downloadModel,
        onModelChange: (serviceId, path) => uiRender.filterVisionModels(serviceId, path, allServiceModels),
        onTabModels: (serviceId) => loadModelStatus(serviceId)
    }, { step: currentStep });
}

async function loadModelStatus(serviceId) {
    try {
        const service = allServices[serviceId];
        const isDisabled = service?.status === 'disabled';
        const models = await api.fetchModels(serviceId);
        allServiceModels[serviceId] = models;
        uiRender.updateModelSelect(serviceId, models, allServiceModels);
        uiRender.renderModelList(serviceId, models, { onDownload: downloadModel, onDelete: deleteModel }, isDisabled);
    } catch (err) { console.error("Load models error:", err); }
}

async function downloadModel(serviceId, modelId, btn) {
    try {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ...';
        const result = await api.postDownloadModel(serviceId, modelId);
        if (result.status === 'success') {
            showToast(result.message || "İndirme başlatıldı", 'success');
        } else {
            showToast(result.message || "Hata oluştu", 'error');
            btn.disabled = false;
        }
    } catch (err) {
        showToast("Bağlantı hatası!", 'error');
        btn.disabled = false;
    }
}

async function deleteModel(serviceId, modelId, btn) {
    if (!confirm("Bu modeli silmek istediğinize emin misiniz?")) {
        return;
    }
    try {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ...';
        const result = await api.postDeleteModel(serviceId, modelId);
        if (result.status === 'success') {
            showToast(result.message || "Model başarıyla silindi", 'success');
            await loadModelStatus(serviceId);
        } else {
            showToast(result.message || "Hata oluştu", 'error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-trash"></i> Sil';
        }
    } catch (err) {
        showToast("Bağlantı hatası!", 'error');
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-trash"></i> Sil';
    }
}

async function installService(id, btn) {
    try {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Baslatiliyor...';
        const service = allServices[id];
        const isCore = isCoreService(service);
        const envSelect = document.getElementById(`env-select-${id}`);
        const envId = envSelect?.value;
        const hardware = envSelect?.options[envSelect.selectedIndex]?.getAttribute('data-hardware');
        const modelFile = isCore ? "" : (document.getElementById(`model-select-${id}`)?.value || "");
        
        // MMPROJ Toggle kontrolü
        let mmprojFile = "";
        const mmprojToggle = document.getElementById(`mmproj-toggle-${id}`);
        if (mmprojToggle && mmprojToggle.checked) {
            mmprojFile = mmprojToggle.dataset.path || "";
        }
        
        // Dinamik parametreleri topla
        const extraParams = {};
        document.querySelectorAll(`#dynamic-params-${id} .dynamic-input`).forEach(input => {
            const paramId = input.dataset.paramId;
            const type = input.dataset.type;
            if (type === 'checkbox') {
                extraParams[paramId] = input.checked;
            } else if (type === 'gpu_selector') {
                if (input.checked) extraParams[paramId] = input.value;
            } else if (type === 'gpu_selector_multi') {
                if (input.checked) {
                    if (!extraParams[paramId]) extraParams[paramId] = [];
                    extraParams[paramId].push(input.value);
                }
            } else if (type === 'int') {
                extraParams[paramId] = parseInt(input.value, 10) || 0;
            } else if (type === 'number') {
                extraParams[paramId] = parseFloat(input.value) || 0;
            } else {
                extraParams[paramId] = input.value;
            }
        });

        // Dizi olan (çoklu seçilmiş) parametreleri virgülle birleştir
        for (let key in extraParams) {
            if (Array.isArray(extraParams[key])) {
                extraParams[key] = extraParams[key].join(',');
            }
        }

        const query = `hardware=${hardware || ""}&env_id=${envId || ""}&model_file=${encodeURIComponent(modelFile)}&mmproj_file=${encodeURIComponent(mmprojFile)}&extra_params=${encodeURIComponent(JSON.stringify(extraParams))}`;
        const result = await api.postInstallService(id, query);
        
        if (result.status !== 'success') {
            showToast(result.message || "Hata!", 'error');
            btn.disabled = false;
        } else {
            showToast(result.message || "Kurulum başlatıldı", 'success');
        }
        fetchServices();
    } catch (err) {
        showToast("Hata oluştu!", 'error');
        btn.disabled = false;
    }
}

async function toggleAutostart(id, btn) {
    try {
        btn.disabled = true;
        const result = await api.postToggleAutostart(id);
        if (result.status === 'success') {
            showToast(result.message, 'success');
        } else {
            showToast(result.message || "Hata oluştu", 'error');
        }
        fetchServices();
    } catch (err) {
        showToast("Bağlantı hatası!", 'error');
        btn.disabled = false;
    }
}

async function deleteImage(id, btn) {
    try {
        if (btn) btn.disabled = true;
        const result = await api.postRemoveImage(id);
        if (result.status === 'success') {
            showToast(result.message || "İmaj silindi", 'success');
        } else {
            showToast(result.message || "İmaj silinemedi", 'error');
        }
        if (btn) btn.disabled = false;
    } catch (err) {
        showToast("Bağlantı hatası!", 'error');
        if (btn) btn.disabled = false;
    }
}

async function removeService(id, btn) {
    try {
        if (btn) btn.disabled = true;
        const result = await api.postRemoveService(id);
        if (result.status === 'success') {
            showToast(result.message || "Servis silindi", 'success');
            fetchServices();
            return true;
        } else {
            showToast(result.message || "Silme islemi basarisiz", 'error');
            if (btn) btn.disabled = false;
            return false;
        }
    } catch (err) {
        showToast("Sil endpoint'i henuz hazir degil.", 'error');
        if (btn) btn.disabled = false;
        return false;
    }
}

async function reinstallService(id, btn) {
    try {
        if (btn) btn.disabled = true;
        const service = allServices[id];
        if (!service?.is_installed) {
            showToast("Container bulunamadi. Dogrudan kurulum baslatiliyor.", 'warning');
            return installService(id, btn || document.getElementById(`btn-main-${id}`));
        }

        const removed = await removeService(id, null);
        if (!removed) {
            if (btn) btn.disabled = false;
            return;
        }
        const mainBtn = document.getElementById(`btn-main-${id}`);
        await installService(id, mainBtn || btn);
    } catch (err) {
        showToast("Yeniden kurulum basarisiz.", 'error');
        if (btn) btn.disabled = false;
    }
}

function setStep(step) {
    if (step < 1 || step > steps.length) return;
    currentStep = step;
    updateWizardUI();
    renderFromCache();
    fetchServices();
}

function updateWizardUI() {
    const titleEl = document.getElementById('wizard-title');
    const subtitleEl = document.getElementById('wizard-subtitle');
    const stepsEl = document.getElementById('wizard-steps');
    const backBtn = document.getElementById('btn-step-back');
    const nextBtn = document.getElementById('btn-step-next');

    const stepInfo = steps.find(s => s.id === currentStep);
    if (titleEl && stepInfo) titleEl.innerText = stepInfo.title;
    if (subtitleEl) {
        const subtitle = stepInfo ? stepInfo.subtitle : '';
        subtitleEl.innerText = subtitle;
        subtitleEl.classList.toggle('hidden', !subtitle);
    }

    if (stepsEl) {
        stepsEl.innerHTML = steps.map(step => {
            const isActive = step.id === currentStep;
            const isComplete = step.id < currentStep;
            const statusClass = isActive ? 'active' : (isComplete ? 'complete' : '');
            return `
                <div class="wizard-step ${statusClass}" data-step="${step.id}">
                    <span class="wizard-step-number">${step.id}</span>
                    <span class="wizard-step-label">${step.title}</span>
                </div>
            `;
        }).join('');

        stepsEl.querySelectorAll('.wizard-step').forEach(stepEl => {
            stepEl.onclick = () => {
                const next = Number(stepEl.dataset.step);
                // Precheck runs when going forward from step 1
                if (!Number.isNaN(next)) {
                     // We don't block clicking past step 1 directly here for simplicity, but ideally we should
                     setStep(next);
                }
            };
        });
    }

    const envStepEl = document.getElementById('env-selection-step');
    const gridEl = document.getElementById('services-grid');
    if (envStepEl && gridEl) {
        if (currentStep === 1) {
            envStepEl.classList.remove('hidden');
            gridEl.classList.add('hidden');
        } else {
            envStepEl.classList.add('hidden');
            gridEl.classList.remove('hidden');
        }
    }

    if (backBtn) backBtn.disabled = currentStep === 1;
    if (nextBtn) nextBtn.innerText = currentStep === steps.length ? 'Bitir' : 'Sonraki';
}

function initWizard() {
    const backBtn = document.getElementById('btn-step-back');
    const nextBtn = document.getElementById('btn-step-next');

    if (backBtn) backBtn.onclick = () => setStep(currentStep - 1);
    if (nextBtn) {
        nextBtn.onclick = () => {
            if (currentStep === steps.length) {
                const routerService = allServices['orion-router'];
                const hubService = allServices['orion-hub'];
                
                // Verify both are installed and active (autostart not disabled)
                if (!routerService?.is_installed || routerService?.autostart === false ||
                    !hubService?.is_installed || hubService?.autostart === false) {
                    showToast("Sistemi başlatabilmek için Orion Router ve Orion Hub servislerinin hem kurulu hem de aktif (devre dışı bırakılmamış) olması zorunludur!", "error");
                    return;
                }

                // Immediately open the completion panel in starting mode
                isSystemStarting = true;
                openCompletionPanel();

                api.postStartSystem().then(res => {
                    showToast(res.message || "Sistem başlatılıyor...", "success");
                    
                    // Fast poll for 15 seconds (every 1.5s) to reflect startup changes rapidly
                    let pollCount = 0;
                    const pollInterval = setInterval(() => {
                        fetchServices();
                        pollCount++;
                        if (pollCount >= 10) {
                            clearInterval(pollInterval);
                            isSystemStarting = false;
                            fetchServices(); // final update
                        }
                    }, 1500);
                }).catch(err => {
                    showToast("Sistem başlatılırken hata oluştu!", "error");
                    isSystemStarting = false;
                    fetchServices();
                });
                return;
            }
            setStep(currentStep + 1);
        };
    }

    const closeBtn = document.getElementById('btn-close-completion');
    if (closeBtn) closeBtn.onclick = closeCompletionPanel;

    const completionPanel = document.getElementById('completion-panel');
    if (completionPanel) {
        completionPanel.addEventListener('click', (event) => {
            if (event.target === completionPanel) closeCompletionPanel();
        });
    }

    updateWizardUI();
}

// Initialization
window.addEventListener('DOMContentLoaded', () => {
    api.postKeepAlive();
    initWizard();
    fetchHardware().then(fetchServices);
    setInterval(fetchServices, 5000);
});

window.addEventListener('beforeunload', () => {
    api.sendShutdownBeacon();
});
