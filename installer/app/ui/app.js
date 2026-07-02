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
        titleKey: 'ui_step_2_title',
        subtitleKey: 'ui_step_2_sub'
    },
    {
        id: 2,
        titleKey: 'ui_step_3_title',
        subtitleKey: 'ui_step_3_sub'
    },
    {
        id: 3,
        titleKey: 'ui_step_4_title',
        subtitleKey: 'ui_step_4_sub'
    }
];

async function fetchHardware() {
    try {
        const info = await api.fetchHardware();

        // GPU İsmi
        const gpuNameEl = document.getElementById('hw-gpu-name');
        if (gpuNameEl) gpuNameEl.innerText = info.DETECTED_GPU_NAME || window.t('lbl_unknown');

        // VRAM
        const vramEl = document.getElementById('hw-vram-gb');
        if (vramEl) vramEl.innerText = info.DETECTED_VRAM_GB ? `${info.DETECTED_VRAM_GB} GB` : "0 GB";

        // CPU
        const cpuEl = document.getElementById('hw-cpu-name');
        if (cpuEl) cpuEl.innerText = info.DETECTED_CPU || window.t('lbl_unknown');

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

        // Mode Badge
        const modeText = document.getElementById('mode-text');
        if (modeText) {
            const mode = info.install_mode || "docker";
            modeText.innerText = mode.charAt(0).toUpperCase() + mode.slice(1);
        }

        // GPU Vendor
        window.orionGpuVendor = (info.DETECTED_GPU_VENDOR || 'cpu').toLowerCase();

        // GPU List
        try {
            window.orionGpuList = info.DETECTED_GPU_LIST ? JSON.parse(info.DETECTED_GPU_LIST) : [];
        } catch (e) {
            window.orionGpuList = [];
        }

    } catch (err) {
        console.error("Hardware fetch error:", err);
    }
}

function getServiceRuntimeState(service) {
    if (!service?.is_installed) return { label: window.t('status_uninstalled'), className: 'status-missing' };
    if (service.autostart === false) return { label: window.t('status_disabled'), className: 'status-stopped' };
    if (service.is_running) return { label: window.t('status_running'), className: 'status-running' };
    if (isSystemStarting) return { label: window.t('status_starting'), className: 'status-starting' };
    return { label: window.t('status_stopped'), className: 'status-stopped' };
}

function renderCompletionPanel() {
    const summaryEl = document.getElementById('completion-summary');
    const listEl = document.getElementById('completion-status-list');
    if (!summaryEl || !listEl) return;

    const services = Object.values(allServices).filter(s => s.status !== 'disabled');
    const activeCount = services.filter(s => s.is_installed && s.autostart !== false).length;
    const installedCount = services.filter(s => s.is_installed).length;

    summaryEl.innerHTML = `
        <span class="summary-pill">${window.t('ui_summary_total', services.length)}</span>
        <span class="summary-pill">${window.t('ui_summary_active', activeCount)}</span>
        <span class="summary-pill">${window.t('ui_summary_installed', installedCount)}</span>
    `;

    listEl.innerHTML = services.map(service => {
        const state = getServiceRuntimeState(service);
        return `
            <div class="completion-status-item">
                <div>
                    <div class="completion-service-name">${window.t_service_name(service)}</div>
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
                        showToast(window.t('msg_service_started', window.t_service_name(service)), 'success');
                    } else {
                        showToast(window.t('msg_install_failed', window.t_service_name(service)), 'error');
                    }
                }

                // Track service startup transition
                if (!prevState.is_running && service.is_running) {
                    showToast(window.t('msg_service_started', window.t_service_name(service)), 'success');
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
        uiRender.renderModelList(serviceId, models, { onDownload: downloadModel, onDelete: deleteModel, onCancel: cancelDownload }, isDisabled);
    } catch (err) { console.error("Load models error:", err); }
}

async function downloadModel(serviceId, modelId, btn) {
    try {
        btn.disabled = true;
        btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${window.t('status_preparing')}`;
        const result = await api.postDownloadModel(serviceId, modelId);
        if (result.status === 'success') {
            showToast(result.message || window.t('msg_download_started'), 'success');
        } else {
            showToast(result.message || window.t('msg_error'), 'error');
            btn.disabled = false;
        }
    } catch (err) {
        showToast(window.t('msg_error'), 'error');
        btn.disabled = false;
    }
}

async function cancelDownload(serviceId, modelId, btn) {
    try {
        btn.disabled = true;
        const result = await api.postCancelDownload(serviceId, modelId);
        if (result.status === 'success') {
            showToast(result.message, 'success');
        } else {
            showToast(result.message || window.t('msg_error'), 'error');
            btn.disabled = false;
        }
    } catch (err) {
        showToast(window.t('msg_error'), 'error');
        btn.disabled = false;
    }
}

function showConfirm(title, message) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        const titleEl = document.getElementById('confirm-modal-title');
        const messageEl = document.getElementById('confirm-modal-message');
        const btnYes = document.getElementById('confirm-modal-yes');
        const btnNo = document.getElementById('confirm-modal-no');

        titleEl.innerText = title;
        messageEl.innerText = message;

        modal.classList.remove('hidden');

        const cleanup = (value) => {
            modal.classList.add('hidden');
            btnYes.onclick = null;
            btnNo.onclick = null;
            modal.onclick = null;
            window.removeEventListener('keydown', handleKeyDown);
            resolve(value);
        };

        const handleKeyDown = (e) => {
            if (e.key === 'Escape') {
                cleanup(false);
            } else if (e.key === 'Enter') {
                cleanup(true);
            }
        };

        btnYes.onclick = () => cleanup(true);
        btnNo.onclick = () => cleanup(false);
        modal.onclick = (e) => {
            if (e.target === modal) cleanup(false);
        };
        window.addEventListener('keydown', handleKeyDown);
    });
}

async function deleteModel(serviceId, modelId, btn) {
    const confirmed = await showConfirm(window.t('confirm_delete_model_title'), window.t('confirm_delete_model_msg'));
    if (!confirmed) {
        return;
    }
    try {
        btn.disabled = true;
        btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${window.t('status_preparing')}`;
        const result = await api.postDeleteModel(serviceId, modelId);
        if (result.status === 'success') {
            showToast(result.message || window.t('msg_service_started', ''), 'success');
            await loadModelStatus(serviceId);
        } else {
            showToast(result.message || window.t('msg_error'), 'error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-trash"></i>';
        }
    } catch (err) {
        showToast(window.t('msg_error'), 'error');
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-trash"></i>';
    }
}

async function installService(id, btn) {
    try {
        btn.disabled = true;
        btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${window.t('status_starting')}`;
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
            showToast(result.message || window.t('msg_error'), 'error');
            btn.disabled = false;
        } else {
            showToast(result.message || window.t('status_preparing'), 'success');
        }
        fetchServices();
    } catch (err) {
        showToast(window.t('msg_error'), 'error');
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
            showToast(result.message || window.t('msg_error'), 'error');
        }
        fetchServices();
    } catch (err) {
        showToast(window.t('msg_conn_error'), 'error');
        btn.disabled = false;
    }
}

async function deleteImage(id, btn) {
    const service = allServices[id];
    const serviceName = service ? window.t_service_name(service) : '';
    const msg = serviceName
        ? window.t('confirm_delete_image_msg_named', serviceName)
        : window.t('confirm_delete_image_msg');
    const confirmed = await showConfirm(window.t('confirm_delete_image_title'), msg);
    if (!confirmed) {
        return;
    }
    try {
        if (btn) btn.disabled = true;
        const result = await api.postRemoveImage(id);
        if (result.status === 'success') {
            showToast(result.message || window.t('msg_image_deleted'), 'success');
        } else {
            showToast(result.message || window.t('msg_image_delete_failed'), 'error');
        }
        if (btn) btn.disabled = false;
    } catch (err) {
        showToast(window.t('msg_conn_error'), 'error');
        if (btn) btn.disabled = false;
    }
}

async function removeService(id, btn) {
    try {
        if (btn) btn.disabled = true;
        const result = await api.postRemoveService(id);
        if (result.status === 'success') {
            showToast(result.message || window.t('msg_service_deleted'), 'success');
            fetchServices();
            return true;
        } else {
            showToast(result.message || window.t('msg_service_delete_failed'), 'error');
            if (btn) btn.disabled = false;
            return false;
        }
    } catch (err) {
        showToast(window.t('msg_delete_endpoint_not_ready'), 'error');
        if (btn) btn.disabled = false;
        return false;
    }
}

async function reinstallService(id, btn) {
    try {
        if (btn) btn.disabled = true;
        const service = allServices[id];
        if (!service?.is_installed) {
            showToast(window.t('msg_container_not_found_installing'), 'warning');
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
        showToast(window.t('msg_reinstall_failed'), 'error');
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
    if (titleEl && stepInfo) {
        const stepTitle = window.t(stepInfo.titleKey);
        if (titleEl.innerText !== stepTitle) {
            titleEl.innerText = stepTitle;
        }
    }
    if (subtitleEl) {
        const subtitle = stepInfo && stepInfo.subtitleKey ? window.t(stepInfo.subtitleKey) : '';
        if (subtitleEl.innerText !== subtitle) {
            subtitleEl.innerText = subtitle;
            subtitleEl.classList.toggle('hidden', !subtitle);
        }
    }

    if (stepsEl) {
        const newHtml = steps.map(step => {
            const isActive = step.id === currentStep;
            const isComplete = step.id < currentStep;
            const statusClass = isActive ? 'active' : (isComplete ? 'complete' : '');
            return `
                <div class="wizard-step ${statusClass}" data-step="${step.id}">
                    <span class="wizard-step-number">${step.id}</span>
                    <span class="wizard-step-label">${window.t(step.titleKey)}</span>
                </div>
            `;
        }).join('');

        if (stepsEl.innerHTML.trim() !== newHtml.trim()) {
            stepsEl.innerHTML = newHtml;
            stepsEl.querySelectorAll('.wizard-step').forEach(stepEl => {
                stepEl.onclick = () => {
                    const next = Number(stepEl.dataset.step);
                    if (!Number.isNaN(next)) {
                        setStep(next);
                    }
                };
            });
        }
    }



    if (backBtn) backBtn.disabled = currentStep === 1;
    if (nextBtn) nextBtn.innerText = currentStep === steps.length ? window.t('btn_finish') : window.t('ui_btn_next');
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
                    showToast(window.t('msg_core_req'), "error");
                    return;
                }

                // Immediately open the completion panel in starting mode
                isSystemStarting = true;
                openCompletionPanel();

                api.postStartSystem().then(res => {
                    showToast(res.message || window.t('msg_system_starting'), "success");

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
                    showToast(window.t('msg_system_start_error'), "error");
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
    // Setup Language Selector
    const langSelect = document.getElementById('lang-select');
    if (langSelect) {
        langSelect.value = localStorage.getItem('orion_lang') || window.orionLang || 'en';
        langSelect.addEventListener('change', (e) => {
            if (window.setLanguage) window.setLanguage(e.target.value);
        });
    }

    // Re-render strings when language changes
    window.addEventListener('languageChanged', () => {
        updateWizardUI();
        renderFromCache();

        // Update all models lists and selects immediately from cache
        Object.keys(allServices).forEach(serviceId => {
            const service = allServices[serviceId];
            if (service && service.status !== 'disabled' && !isCoreService(service) && allServiceModels[serviceId]) {
                const models = allServiceModels[serviceId];
                const isDisabled = service.status === 'disabled';
                uiRender.updateModelSelect(serviceId, models, allServiceModels);
                uiRender.renderModelList(serviceId, models, { onDownload: downloadModel, onDelete: deleteModel }, isDisabled);
            }
        });

        if (!document.getElementById('completion-panel')?.classList.contains('hidden')) {
            renderCompletionPanel();
        }
    });

    api.postKeepAlive();
    initWizard();
    fetchHardware().then(fetchServices);
    setInterval(fetchServices, 5000);
});

window.addEventListener('beforeunload', () => {
    api.sendShutdownBeacon();
});
