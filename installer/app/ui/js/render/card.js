import { renderParameters } from './parameters.js';

// Global click, resize, and blur listeners
const closeAllDropdowns = () => {
    document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.add('hidden'));
    document.querySelectorAll('.btn-split-toggle').forEach(b => b.setAttribute('aria-expanded', 'false'));
};

document.addEventListener('click', e => !e.target.closest('.btn-split-toggle') && closeAllDropdowns());
window.addEventListener('resize', closeAllDropdowns);
window.addEventListener('blur', closeAllDropdowns);

const isCoreService = s => ['orion-hub', 'core', 'hub', 'router'].includes(s?.id) || ['core', 'hub', 'router'].includes(s?.category);

export function createServiceCard(service, grid) {
    const card = document.createElement('div');
    card.id = `service-card-${service.id}`;
    card.className = `service-card panel ${isCoreService(service) ? 'core-card' : ''}`;
    grid.appendChild(card);
    return card;
}

export function updateCardStatusClasses(card, isDisabled) {
    card.classList.toggle('disabled-service', isDisabled);
}

export function renderCardSkeleton(card, service, isDisabled, handlers, viewMode) {
    const isWindows = (window.orionOsPlatform || '').toLowerCase() === 'windows';
    const gpuVendor = (window.orionGpuVendor || '').toLowerCase();

    const processedEnvs = (service.supported_environments || []).map(env => {
        const hw = (env.hardware || '').toLowerCase();
        const disabled = isWindows && ['amd', 'vulkan'].includes(hw);
        return {
            ...env, hw, disabled,
            title: disabled ? ` title="${window.t('lbl_linux_only', hw === 'amd' ? 'ROCm' : 'Vulkan')}"` : ''
        };
    });

    const enabledEnvs = processedEnvs.filter(e => !e.disabled);
    let selectedId = enabledEnvs.find(e => e.hw === gpuVendor)?.id
        || (gpuVendor === 'amd' ? enabledEnvs.find(e => e.hw === 'vulkan')?.id : null)
        || enabledEnvs.find(e => e.hw === 'cpu')?.id
        || (enabledEnvs[0]?.id || '');

    const envOptions = processedEnvs.map(env => {
        const envNameKey = `env_name_${env.id}`.toLowerCase();
        const envName = window.t(envNameKey) !== envNameKey ? window.t(envNameKey) : env.name;
        return `<option value="${env.id}" data-hardware="${env.hw}" ${env.disabled ? 'disabled' : ''} ${env.id === selectedId ? 'selected' : ''}${env.title}>${envName}${env.disabled ? window.t('lbl_linux_only_suffix') : ''}</option>`;
    }).join('');

    const installMode = viewMode === 'install';
    const paramsHtml = renderParameters(service, isDisabled);
    const envHtml = isCoreService(service) ? '' : `
        <div class="field">
            <label class="field-label" for="env-select-${service.id}">${window.t('lbl_hardware')}</label>
            <select id="env-select-${service.id}" class="field-input" ${isDisabled ? 'disabled' : ''}>${envOptions}</select>
        </div>`;

    const modelHtml = isCoreService(service) ? '' : `
        <div class="field">
            <label class="field-label" for="model-select-${service.id}">${window.t('lbl_model')}</label>
            <select id="model-select-${service.id}" class="field-input" ${isDisabled ? 'disabled' : ''}>
                <option value="">${window.t('lbl_select_model')}</option>
            </select>
        </div>
        ${['llm', 'embedding'].includes(service.category) ? `<div id="mmproj-toggle-container-${service.id}" class="mmproj-container hidden"></div>` : ''}
    `;

    const generalHtml = installMode && (envHtml || paramsHtml || modelHtml) ? `
        <div class="card-section">
            ${envHtml}
            <div id="dynamic-params-${service.id}" class="params-container">${paramsHtml}</div>
            ${modelHtml}
        </div>` : '';

    const newHtml = `
        <div class="service-header">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <h3 class="service-name" style="margin-bottom: 0;">${window.t_service_name(service)}</h3>
                <div id="status-dot-container-${service.id}"></div>
            </div>
            <p class="service-desc">${window.t_service_desc(service)}</p>
        </div>
        ${installMode ? `<div id="general-${service.id}" class="tab-content active">${generalHtml}</div>` : ''}
        ${viewMode === 'models-only' ? `<div id="models-${service.id}" class="tab-content active"><div class="card-section"><div id="model-list-${service.id}" class="model-list">${window.t('lbl_scanning')}</div></div></div>` : ''}
        <div class="service-footer-dynamic"></div>
    `;

    if (card.innerHTML !== newHtml) {
        card.innerHTML = newHtml;
        setupCardInteractions(card, service, handlers);
    }
}

export function updateCardDynamicContent(card, service, isDisabled, handlers, viewMode) {
    const footer = card.querySelector('.service-footer-dynamic');
    if (!footer) return;

    if (viewMode === 'models-only') {
        if (footer.innerHTML) footer.innerHTML = '';
        return;
    }

    const stateKey = `${service.is_installed}_${service.is_installing}_${service.autostart !== false}_${service.is_running}_${service.is_starting}_${isDisabled}_${service.install_error || ''}_${window.orionLang || 'en'}`;
    if (footer.dataset.stateKey === stateKey) return;
    footer.dataset.stateKey = stateKey;

    // [!] SARI IŞIK DÜZELTMESİ: is_installing eklendi
    let statusLabel, statusClass;
    if (service.is_installing || service.is_starting) {
        statusLabel = window.t('status_starting');
        statusClass = 'status-starting';
    } else if (!service.is_installed) {
        statusLabel = window.t('status_uninstalled');
        statusClass = 'status-missing';
    } else if (service.is_running) {
        statusLabel = window.t('status_running');
        statusClass = 'status-running';
    } else {
        statusLabel = window.t('status_stopped');
        statusClass = 'status-stopped';
    }

    const dotContainer = card.querySelector(`#status-dot-container-${service.id}`);
    if (dotContainer) dotContainer.innerHTML = `<div class="status-badge ${statusClass}" title="${statusLabel}" style="margin: 0;"><span class="status-dot"></span></div>`;

    let actionHtml = '';
    if (isDisabled && !service.is_installed) {
        actionHtml = `<button class="btn" disabled>${window.t('status_unavailable')}</button>`;
    } else if (service.is_installing) {
        actionHtml = `<button class="btn btn-primary" disabled><i class="fas fa-spinner fa-spin"></i> ${window.t('status_preparing')}</button>`;
    } else {
        const isAuto = service.autostart !== false;
        const isCore = isCoreService(service);
        const btnClass = !service.is_installed ? 'btn btn-primary' : (isCore ? 'btn btn-success' : (isAuto ? 'btn btn-danger' : 'btn btn-success'));
        const btnLabel = !service.is_installed ? window.t('btn_install') : (isCore ? window.t('status_active') : (isAuto ? window.t('btn_disable') : window.t('btn_enable')));
        const btnAttr = isCore && service.is_installed ? 'disabled style="cursor: default;"' : '';

        const dropdownHtml = service.is_installed ? `
            <div class="split-dropdown">
                <button class="btn btn-split-toggle" id="btn-menu-${service.id}" aria-expanded="false" aria-controls="menu-${service.id}" title="${window.t('lbl_other_actions')}">
                    <i class="fas fa-chevron-down"></i>
                </button>
                <div class="dropdown-menu hidden" id="menu-${service.id}">
                    <button class="dropdown-item" id="btn-reinstall-${service.id}">${window.t('btn_reinstall')}</button>
                    <button class="dropdown-item danger" id="btn-remove-${service.id}">${window.t('btn_remove')}</button>
                    <button class="dropdown-item danger" id="btn-delete-image-${service.id}">${window.t('btn_delete_image')}</button>
                </div>
            </div>` : '';

        actionHtml = `<div class="split-action"><button class="${btnClass}" id="btn-main-${service.id}" ${btnAttr}>${btnLabel}</button>${dropdownHtml}</div>`;
    }

    const errorHtml = service.install_error ? `<div class="install-error" style="color: #ef4444; font-size: 0.85em; padding: 8px 12px; background: rgba(239, 68, 68, 0.1); border-radius: 4px; margin: 0 16px 12px 16px;"><i class="fas fa-exclamation-triangle" style="margin-right: 4px;"></i> ${service.install_error}</div>` : '';

    footer.innerHTML = `${errorHtml}<div class="service-footer"><div class="category-tag" style="margin-bottom: 0;">${service.category.toUpperCase()}</div><div class="footer-actions">${actionHtml}</div></div>`;

    // Bind events
    const bindEvent = (id, handler) => {
        const el = card.querySelector(id);
        if (el) el.onclick = (e) => handler(e, el);
    };

    bindEvent(`#btn-main-${service.id}`, () => service.is_installed && !isCoreService(service) ? handlers.onToggleAutostart(service.id, document.getElementById(`btn-main-${service.id}`)) : handlers.onStart(service.id, document.getElementById(`btn-main-${service.id}`)));
    bindEvent(`#btn-reinstall-${service.id}`, () => handlers.onReinstall(service.id, document.getElementById(`btn-reinstall-${service.id}`)));
    bindEvent(`#btn-remove-${service.id}`, () => handlers.onRemove(service.id, document.getElementById(`btn-remove-${service.id}`)));
    bindEvent(`#btn-delete-image-${service.id}`, () => handlers.onDeleteImage(service.id, document.getElementById(`btn-delete-image-${service.id}`)));

    bindEvent(`#btn-menu-${service.id}`, (e, btn) => {
        e.stopPropagation();
        const menu = document.getElementById(`menu-${service.id}`);
        if (!menu) return;

        const isHidden = menu.classList.contains('hidden');
        closeAllDropdowns();

        if (isHidden) {
            menu.classList.remove('hidden');
            btn.setAttribute('aria-expanded', 'true');
            menu.classList.toggle('open-up', menu.getBoundingClientRect().bottom > window.innerHeight);
        }
    });
}

export function toggleFormElements(card, isDisabled) {
    card.querySelectorAll('input, select, .tab').forEach(el => {
        if (el.getAttribute('data-type') === 'gpu_selector') return el.setAttribute('disabled', 'true');
        const currentlyDisabled = el.hasAttribute('disabled') || el.style.pointerEvents === 'none';

        if (isDisabled && !currentlyDisabled) {
            el.setAttribute('disabled', 'true');
            if (el.classList.contains('tab')) el.style.pointerEvents = 'none';
        } else if (!isDisabled && currentlyDisabled) {
            el.removeAttribute('disabled');
            if (el.classList.contains('tab')) el.style.pointerEvents = 'auto';
        }
    });
}

function setupCardInteractions(card, service, handlers) {
    card.querySelectorAll('.tab').forEach(tab => {
        tab.onclick = () => {
            card.querySelectorAll('.tab, .tab-content').forEach(el => el.classList.remove('active'));
            tab.classList.add('active');
            const target = card.querySelector(`#${tab.dataset.tab}`);
            if (target) target.classList.add('active');
            if (tab.dataset.tab?.startsWith('models-')) handlers.onTabModels(service.id);
        };
    });

    card.querySelector(`#model-select-${service.id}`)?.addEventListener('change', e => handlers.onModelChange(service.id, e.target.value));

    const envSelect = card.querySelector(`#env-select-${service.id}`);
    const gpuField = card.querySelector(`#gpu-selector-field-${service.id}`);
    if (envSelect && gpuField) {
        const updateGpu = () => gpuField.style.display = envSelect.options[envSelect.selectedIndex]?.getAttribute('data-hardware')?.toLowerCase() === 'cpu' ? 'none' : 'block';
        envSelect.addEventListener('change', updateGpu);
        updateGpu();
    }

    const gpuCheckboxes = card.querySelectorAll(`.dynamic-input[data-type="gpu_selector_multi"]`);
    gpuCheckboxes.forEach(cb => cb.addEventListener('change', () => {
        if (!Array.from(gpuCheckboxes).some(c => c.checked)) cb.checked = true;
    }));
}