import * as api from './js/api.js';
import * as uiRender from './js/ui-render.js';
import { showToast } from './js/ui-utils.js';

let previousServiceStates = {}, allServiceModels = {}, allServices = {};
let currentStep = 1, isSystemStarting = false;

const isCoreService = (s) => ['orion-hub', 'core', 'hub', 'router'].includes(s?.id) || ['core', 'hub', 'router'].includes(s?.category);

const steps = [
    { id: 1, titleKey: 'ui_step_2_title', subtitleKey: 'ui_step_2_sub' },
    { id: 2, titleKey: 'ui_step_3_title', subtitleKey: 'ui_step_3_sub' },
    { id: 3, titleKey: 'ui_step_4_title', subtitleKey: 'ui_step_4_sub' }
];

async function fetchHardware() {
    try {
        const info = await api.fetchHardware();
        const setEl = (id, val) => document.getElementById(id) && (document.getElementById(id).innerText = val);

        setEl('hw-gpu-name', info.DETECTED_GPU_NAME || window.t('lbl_unknown'));
        setEl('hw-vram-gb', info.DETECTED_VRAM_GB ? `${info.DETECTED_VRAM_GB} GB` : "0 GB");
        setEl('hw-cpu-name', info.DETECTED_CPU || window.t('lbl_unknown'));

        const osIcon = document.getElementById('os-icon');
        if (osIcon) {
            const os = info.OS_PLATFORM || "unknown";
            window.orionOsPlatform = os;
            setEl('os-text', os.charAt(0).toUpperCase() + os.slice(1));
            osIcon.className = os === 'windows' ? 'fab fa-windows' : (os === 'linux' ? 'fab fa-linux' : 'fas fa-desktop');
        }

        if (info.install_mode) setEl('mode-text', info.install_mode.charAt(0).toUpperCase() + info.install_mode.slice(1));

        window.orionGpuVendor = (info.DETECTED_GPU_VENDOR || 'cpu').toLowerCase();
        try { window.orionGpuList = info.DETECTED_GPU_LIST ? JSON.parse(info.DETECTED_GPU_LIST) : []; }
        catch { window.orionGpuList = []; }
    } catch (err) { console.error("Hardware fetch error:", err); }
}

function getServiceRuntimeState(service) {
    if (!service?.is_installed) return { label: window.t('status_uninstalled'), className: 'status-missing' };
    if (service.autostart === false) return { label: window.t('status_disabled'), className: 'status-stopped' };
    if (service.is_running) return { label: window.t('status_running'), className: 'status-running' };

    // [!] Sarı ışığın Finish ekranında da yanması için service.is_starting eklendi
    if (service.is_installing || service.is_starting || isSystemStarting) return { label: window.t('status_starting'), className: 'status-starting' };

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
            </div>`;
    }).join('');
}

const openCompletionPanel = () => {
    const p = document.getElementById('completion-panel');
    if (p) { renderCompletionPanel(); p.classList.remove('hidden'); }
};

const closeCompletionPanel = () => document.getElementById('completion-panel')?.classList.add('hidden');

async function fetchServices() {
    try {
        const services = await api.fetchServices();
        services.forEach(service => {
            const prevState = previousServiceStates[service.id];
            if (prevState) {
                if (prevState.is_installing && !service.is_installing) {
                    showToast(window.t(service.is_installed ? 'msg_service_started' : 'msg_install_failed', window.t_service_name(service)), service.is_installed ? 'success' : 'error');
                }
                if (!prevState.is_running && service.is_running) {
                    showToast(window.t('msg_service_started', window.t_service_name(service)), 'success');
                }
            }
            allServices[service.id] = service;
            previousServiceStates[service.id] = { is_installing: service.is_installing, is_running: service.is_running };

            if (service.status !== 'disabled' && !isCoreService(service)) loadModelStatus(service.id);
        });

        renderFromCache();

        const p = document.getElementById('completion-panel');
        if (p && !p.classList.contains('hidden')) renderCompletionPanel();
        updateWizardUI();
    } catch (err) { console.error("Services fetch error:", err); }
}

function renderFromCache() {
    const services = Object.values(allServices);
    if (!services.length) return;
    uiRender.renderServices(services, previousServiceStates, allServiceModels, {
        onStart: installService, onToggleAutostart: toggleAutostart,
        onReinstall: reinstallService, onRemove: removeService,
        onDeleteImage: deleteImage, onDownload: downloadModel,
        onModelChange: (sid, path) => uiRender.filterVisionModels(sid, path, allServiceModels),
        onTabModels: loadModelStatus
    }, { step: currentStep });
}

async function loadModelStatus(serviceId) {
    try {
        const models = await api.fetchModels(serviceId);
        allServiceModels[serviceId] = models;
        uiRender.updateModelSelect(serviceId, models, allServiceModels);
        uiRender.renderModelList(serviceId, models, { onDownload: downloadModel, onDelete: deleteModel, onCancel: cancelDownload }, allServices[serviceId]?.status === 'disabled');
    } catch (err) { console.error("Load models error:", err); }
}

async function handleAction(btn, actionStr, apiCall, onSuccess, onFail) {
    try {
        if (btn) { btn.disabled = true; btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${window.t(actionStr)}`; }
        const res = await apiCall();
        if (res.status === 'success') { showToast(res.message || window.t('msg_success'), 'success'); if (onSuccess) onSuccess(); }
        else { showToast(res.message || window.t('msg_error'), 'error'); if (btn) btn.disabled = false; if (onFail) onFail(); }
        return res.status === 'success';
    } catch {
        showToast(window.t('msg_error'), 'error');
        if (btn) btn.disabled = false;
        if (onFail) onFail();
        return false;
    }
}

const downloadModel = (sid, mid, btn) => handleAction(btn, 'status_preparing', () => api.postDownloadModel(sid, mid), null, () => { btn.innerHTML = window.t('btn_download'); });
const cancelDownload = (sid, mid, btn) => handleAction(btn, 'status_cancelling', () => api.postCancelDownload(sid, mid));

function showConfirm(title, message) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        document.getElementById('confirm-modal-title').innerText = title;
        document.getElementById('confirm-modal-message').innerText = message;
        modal.classList.remove('hidden');

        const cleanup = (val) => {
            modal.classList.add('hidden');
            window.removeEventListener('keydown', handleKeyDown);
            resolve(val);
        };
        const handleKeyDown = (e) => e.key === 'Escape' ? cleanup(false) : (e.key === 'Enter' ? cleanup(true) : null);

        document.getElementById('confirm-modal-yes').onclick = () => cleanup(true);
        document.getElementById('confirm-modal-no').onclick = () => cleanup(false);
        modal.onclick = (e) => e.target === modal && cleanup(false);
        window.addEventListener('keydown', handleKeyDown);
    });
}

async function deleteModel(sid, mid, btn) {
    if (await showConfirm(window.t('confirm_delete_model_title'), window.t('confirm_delete_model_msg'))) {
        handleAction(btn, 'status_preparing', () => api.postDeleteModel(sid, mid), () => loadModelStatus(sid), () => { btn.innerHTML = '<i class="fas fa-trash"></i>'; });
    }
}

async function installService(id, btn) {
    try {
        btn.disabled = true;
        btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${window.t('status_starting')}`;

        const envId = document.getElementById(`env-select-${id}`)?.value || "";
        const hw = document.getElementById(`env-select-${id}`)?.options[document.getElementById(`env-select-${id}`).selectedIndex]?.getAttribute('data-hardware') || "";
        const modelFile = isCoreService(allServices[id]) ? "" : (document.getElementById(`model-select-${id}`)?.value || "");
        const mmprojFile = document.getElementById(`mmproj-toggle-${id}`)?.checked ? document.getElementById(`mmproj-toggle-${id}`).dataset.path : "";

        const extraParams = Array.from(document.querySelectorAll(`#dynamic-params-${id} .dynamic-input`)).reduce((acc, input) => {
            const { paramId, type } = input.dataset;
            if (type === 'checkbox') acc[paramId] = input.checked;
            else if (['gpu_selector', 'gpu_selector_multi'].includes(type) && input.checked) {
                acc[paramId] = type === 'gpu_selector_multi' ? [...(acc[paramId] || []), input.value] : input.value;
            }
            else if (type === 'int') acc[paramId] = parseInt(input.value, 10) || 0;
            else if (type === 'number') acc[paramId] = parseFloat(input.value) || 0;
            else acc[paramId] = input.value;
            return acc;
        }, {});

        for (let key in extraParams) if (Array.isArray(extraParams[key])) extraParams[key] = extraParams[key].join(',');

        const query = `hardware=${hw}&env_id=${envId}&model_file=${encodeURIComponent(modelFile)}&mmproj_file=${encodeURIComponent(mmprojFile)}&extra_params=${encodeURIComponent(JSON.stringify(extraParams))}`;

        const result = await api.postInstallService(id, query);
        showToast(result.message || (result.status === 'success' ? window.t('status_preparing') : window.t('msg_error')), result.status);
        if (result.status !== 'success') btn.disabled = false;
        fetchServices();
    } catch { showToast(window.t('msg_error'), 'error'); btn.disabled = false; }
}

const toggleAutostart = async (id, btn) => { btn.disabled = true; await handleAction(null, '', () => api.postToggleAutostart(id), fetchServices, () => { btn.disabled = false; }); };

async function deleteImage(id, btn) {
    const sName = allServices[id] ? window.t_service_name(allServices[id]) : '';
    if (await showConfirm(window.t('confirm_delete_image_title'), sName ? window.t('confirm_delete_image_msg_named', sName) : window.t('confirm_delete_image_msg'))) {
        if (btn) btn.disabled = true;
        await handleAction(null, '', () => api.postRemoveImage(id), null, () => { if (btn) btn.disabled = false; });
    }
}

async function removeService(id, btn) {
    if (btn) btn.disabled = true;
    return await handleAction(null, '', () => api.postRemoveService(id), fetchServices, () => { if (btn) btn.disabled = false; });
}

async function reinstallService(id, btn) {
    if (btn) btn.disabled = true;
    if (!allServices[id]?.is_installed) {
        showToast(window.t('msg_container_not_found_installing'), 'warning');
        return installService(id, btn || document.getElementById(`btn-main-${id}`));
    }
    if (await removeService(id, null)) await installService(id, document.getElementById(`btn-main-${id}`) || btn);
    else if (btn) btn.disabled = false;
}

function setStep(step) {
    if (step < 1 || step > steps.length) return;
    currentStep = step;
    updateWizardUI();
    renderFromCache();
    fetchServices();
}

function updateWizardUI() {
    const stepInfo = steps.find(s => s.id === currentStep);
    const setEl = (id, val, hideIfEmpty = false) => {
        const el = document.getElementById(id);
        if (el) { el.innerText = val; if (hideIfEmpty) el.classList.toggle('hidden', !val); }
    };

    if (stepInfo) {
        setEl('wizard-title', window.t(stepInfo.titleKey));
        setEl('wizard-subtitle', stepInfo.subtitleKey ? window.t(stepInfo.subtitleKey) : '', true);
    }

    const stepsEl = document.getElementById('wizard-steps');
    if (stepsEl) {
        stepsEl.innerHTML = steps.map(step => `
            <div class="wizard-step ${step.id === currentStep ? 'active' : (step.id < currentStep ? 'complete' : '')}" data-step="${step.id}">
                <span class="wizard-step-number">${step.id}</span>
                <span class="wizard-step-label">${window.t(step.titleKey)}</span>
            </div>`).join('');
        stepsEl.querySelectorAll('.wizard-step').forEach(el => el.onclick = () => setStep(Number(el.dataset.step) || currentStep));
    }

    const backBtn = document.getElementById('btn-step-back'), nextBtn = document.getElementById('btn-step-next');
    if (backBtn) backBtn.disabled = currentStep === 1;
    if (nextBtn) nextBtn.innerText = currentStep === steps.length ? window.t('btn_finish') : window.t('ui_btn_next');
}

function initWizard() {
    document.getElementById('btn-step-back')?.addEventListener('click', () => setStep(currentStep - 1));
    const nextBtn = document.getElementById('btn-step-next');

    if (nextBtn) {
        nextBtn.onclick = () => {
            if (currentStep !== steps.length) return setStep(currentStep + 1);

            const r = allServices['orion-router'], h = allServices['orion-hub'];
            if (!r?.is_installed || r?.autostart === false || !h?.is_installed || h?.autostart === false) {
                return showToast(window.t('msg_core_req'), "error");
            }

            isSystemStarting = true;
            openCompletionPanel();

            api.postStartSystem().then(() => {
                showToast(window.t('msg_system_starting'), "success");
                let pollCount = 0;
                const pollInterval = setInterval(async () => {
                    await fetchServices();
                    if (Object.values(allServices).filter(s => s.status !== 'disabled' && s.is_installed && s.autostart !== false).every(s => s.is_running) || ++pollCount >= 20) {
                        clearInterval(pollInterval);
                        isSystemStarting = false;
                        renderCompletionPanel();
                    }
                }, 1500);
            }).catch(() => {
                showToast(window.t('msg_system_start_error'), "error");
                isSystemStarting = false;
                fetchServices();
            });
        };
    }

    document.getElementById('btn-close-completion')?.addEventListener('click', closeCompletionPanel);
    document.getElementById('completion-panel')?.addEventListener('click', e => e.target.id === 'completion-panel' && closeCompletionPanel());
    updateWizardUI();
}

window.addEventListener('DOMContentLoaded', () => {
    const langSelect = document.getElementById('lang-select');
    if (langSelect) {
        langSelect.value = localStorage.getItem('orion_lang') || window.orionLang || 'en';
        langSelect.addEventListener('change', e => window.setLanguage && window.setLanguage(e.target.value));
    }

    window.addEventListener('languageChanged', () => {
        updateWizardUI();
        renderFromCache();
        Object.keys(allServices).forEach(sid => {
            const s = allServices[sid];
            if (s && s.status !== 'disabled' && !isCoreService(s) && allServiceModels[sid]) {
                uiRender.updateModelSelect(sid, allServiceModels[sid], allServiceModels);
                uiRender.renderModelList(sid, allServiceModels[sid], { onDownload: downloadModel, onDelete: deleteModel }, s.status === 'disabled');
            }
        });
        if (!document.getElementById('completion-panel')?.classList.contains('hidden')) renderCompletionPanel();
    });

    api.postKeepAlive();
    initWizard();
    fetchHardware().then(fetchServices);
    setInterval(fetchServices, 5000);
});

window.addEventListener('beforeunload', () => api.sendShutdownBeacon());