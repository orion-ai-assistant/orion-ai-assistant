import { renderParameters } from './parameters.js';

// Global click, resize, and blur listeners to make the custom dropdown behave like a native select
const closeAllDropdowns = () => {
    document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.add('hidden'));
    document.querySelectorAll('.btn-split-toggle').forEach(b => b.setAttribute('aria-expanded', 'false'));
};

document.addEventListener('click', (e) => {
    if (!e.target.closest('.btn-split-toggle')) {
        closeAllDropdowns();
    }
});

window.addEventListener('resize', closeAllDropdowns);
window.addEventListener('blur', closeAllDropdowns);

function isCoreService(service) {
    return service?.id === 'orion-hub' || service?.category === 'core' || service?.category === 'hub' || service?.category === 'router';
}

export function createServiceCard(service, grid) {
    const card = document.createElement('div');
    card.id = `service-card-${service.id}`;
    card.className = `service-card panel ${isCoreService(service) ? 'core-card' : ''}`;
    grid.appendChild(card);
    return card;
}

export function updateCardStatusClasses(card, isDisabled) {
    const hasClass = card.classList.contains('disabled-service');
    if (isDisabled && !hasClass) {
        card.classList.add('disabled-service');
    } else if (!isDisabled && hasClass) {
        card.classList.remove('disabled-service');
    }
}

export function renderCardSkeleton(card, service, isDisabled, handlers, viewMode) {
    const isWindows = (window.orionOsPlatform || '').toLowerCase() === 'windows';
    const gpuVendor = (window.orionGpuVendor || '').toLowerCase();

    // Process options to determine disabled state and titles
    const processedEnvs = (service.supported_environments || []).map(env => {
        const hw = (env.hardware || '').toLowerCase();
        const isAmd = hw === 'amd';
        const isVulkan = hw === 'vulkan';
        const disabledOnWindows = isWindows && (isAmd || isVulkan);
        
        let title = '';
        if (disabledOnWindows) {
            const hwName = isAmd ? 'ROCm' : 'Vulkan';
            title = ` title="${hwName} yalnızca Linux'ta desteklenir"`;
        }

        return {
            ...env,
            hw,
            disabled: disabledOnWindows,
            title
        };
    });

    // Select the best default option
    let selectedId = '';
    const enabledEnvs = processedEnvs.filter(e => !e.disabled);
    if (enabledEnvs.length > 0) {
        const matched = enabledEnvs.find(e => e.hw === gpuVendor);
        if (matched) {
            selectedId = matched.id;
        } else if (gpuVendor === 'amd' && enabledEnvs.some(e => e.hw === 'vulkan')) {
            const vulkanOpt = enabledEnvs.find(e => e.hw === 'vulkan');
            selectedId = vulkanOpt.id;
        } else {
            const cpuOpt = enabledEnvs.find(e => e.hw === 'cpu');
            selectedId = cpuOpt ? cpuOpt.id : enabledEnvs[0].id;
        }
    }

    // Render HTML options
    const envOptions = processedEnvs.map(env => {
        const disabledAttr = env.disabled ? 'disabled' : '';
        const selectedAttr = env.id === selectedId ? 'selected' : '';
        const suffix = env.disabled ? ' (Linux only)' : '';
        return `<option value="${env.id}" data-hardware="${env.hw || ''}" ${disabledAttr} ${selectedAttr}${env.title}>${env.name}${suffix}</option>`;
    }).join('');

    const modelsOnly = viewMode === 'models-only';
    const installMode = viewMode === 'install';

    const paramsHtml = renderParameters(service, isDisabled);
    const envHtml = renderEnvironmentSection(service, envOptions, isDisabled);
    const modelHtml = renderModelSelectionSection(service, isDisabled);
    const hasGeneralContent = Boolean(envHtml || paramsHtml || modelHtml);

    const generalHtml = installMode && hasGeneralContent
        ? `
        <div class="card-section">
            ${envHtml}
            <div id="dynamic-params-${service.id}" class="params-container">
                ${paramsHtml}
            </div>
            ${modelHtml}
        </div>`
        : '';

    const modelsHtml = modelsOnly
        ? `
        <div id="models-${service.id}" class="tab-content active">
            <div class="card-section">
                <div id="model-list-${service.id}" class="model-list">Modeller taraniyor...</div>
            </div>
        </div>`
        : '';

    const newHtml = `
        <div class="service-header">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <h3 class="service-name" style="margin-bottom: 0;">${service.name}</h3>
                <div id="status-dot-container-${service.id}"></div>
            </div>
            <p class="service-desc">${service.description}</p>
        </div>
        ${installMode ? `<div id="general-${service.id}" class="tab-content active">${generalHtml}</div>` : ''}
        ${modelsHtml}
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
        if (footer.innerHTML !== '') footer.innerHTML = '';
        return;
    }

    // Performans için durum karşılaştırması (Hiçbir şey değişmediyse DOM güncellemesini atla)
    const stateKey = `${service.is_installed}_${service.is_installing}_${service.autostart !== false}_${service.is_running}_${isDisabled}_${service.install_error || ''}`;
    if (card.dataset.stateKey === stateKey) {
        return;
    }
    card.dataset.stateKey = stateKey;

    const hasInstall = service.is_installed;
    const statusLabel = !hasInstall ? 'Kurulmamis' : (service.is_running ? 'Calisiyor' : 'Durduruldu');
    const statusClass = !hasInstall ? 'status-missing' : (service.is_running ? 'status-running' : 'status-stopped');

    // Update status dot in the header
    const dotContainer = card.querySelector(`#status-dot-container-${service.id}`);
    if (dotContainer) {
        dotContainer.innerHTML = `<div class="status-badge ${statusClass}" title="${statusLabel}" style="margin: 0;"><span class="status-dot"></span></div>`;
    }

    let actionHtml = '';
    const hasContainer = !!service.is_installed;

    if (isDisabled && !service.is_installed) {
        actionHtml = '<button class="btn" disabled>Kullanilamaz</button>';
    } else if (service.is_installing) {
        actionHtml = '<button class="btn btn-primary" disabled><i class="fas fa-spinner fa-spin"></i> Hazırlanıyor...</button>';
    } else {
        const isAutostart = service.autostart !== false;
        let mainBtnClass = !hasContainer ? 'btn btn-primary' : (isAutostart ? 'btn btn-danger' : 'btn btn-success');
        let mainBtnLabel = !hasContainer ? 'Kur' : (isAutostart ? 'Devre Dışı Bırak' : 'Aktifleştir');
        let mainBtnAttr = '';

        if (hasContainer && isCoreService(service)) {
            mainBtnClass = 'btn btn-success';
            mainBtnLabel = 'Aktif';
            mainBtnAttr = 'disabled style="cursor: default;"';
        }

        const mainBtnId = `btn-main-${service.id}`;
        const menuBtnId = `btn-menu-${service.id}`;
        const dropdownId = `menu-${service.id}`;

        const existingDropdown = footer.querySelector(`#menu-${service.id}`);
        const isDropdownOpen = existingDropdown && !existingDropdown.classList.contains('hidden');
        const isDropdownOpenUp = existingDropdown && existingDropdown.classList.contains('open-up');

        const dropdownHtml = hasContainer ? `
            <div class="split-dropdown">
                <button class="btn btn-split-toggle" id="${menuBtnId}" aria-expanded="${isDropdownOpen ? 'true' : 'false'}" aria-controls="${dropdownId}" title="Diger islemler">
                    <i class="fas fa-chevron-down"></i>
                </button>
                <div class="dropdown-menu ${isDropdownOpen ? '' : 'hidden'} ${isDropdownOpenUp ? 'open-up' : ''}" id="${dropdownId}">
                    <button class="dropdown-item" id="btn-reinstall-${service.id}">Yeniden Kur</button>
                    <button class="dropdown-item danger" id="btn-remove-${service.id}">Kaldır</button>
                    <button class="dropdown-item danger" id="btn-delete-image-${service.id}">İmajı Sil</button>
                </div>
            </div>
        ` : '';
        actionHtml = `
            <div class="split-action"><button class="${mainBtnClass}" id="${mainBtnId}" ${mainBtnAttr}>${mainBtnLabel}</button>${dropdownHtml}</div>
        `;
    }

    const errorHtml = service.install_error ? `
        <div class="install-error" style="color: #ef4444; font-size: 0.85em; padding: 8px 12px; background: rgba(239, 68, 68, 0.1); border-radius: 4px; margin: 0 16px 12px 16px;">
            <i class="fas fa-exclamation-triangle" style="margin-right: 4px;"></i> ${service.install_error}
        </div>
    ` : '';

    const newFooterHtml = `
        ${errorHtml}
        <div class="service-footer">
            <div class="category-tag" style="margin-bottom: 0;">${service.category.toUpperCase()}</div>
            <div class="footer-actions">${actionHtml}</div>
        </div>
    `;

    if (footer.innerHTML !== newFooterHtml) {
        footer.innerHTML = newFooterHtml;

        // Re-bind events ONLY if HTML changed
        const mainBtn = card.querySelector(`#btn-main-${service.id}`);
        if (mainBtn) {
            if (hasContainer) {
                if (!isCoreService(service)) {
                    mainBtn.onclick = () => handlers.onToggleAutostart(service.id, mainBtn);
                }
            } else {
                mainBtn.onclick = () => handlers.onStart(service.id, mainBtn);
            }
        }

        const menuBtn = card.querySelector(`#btn-menu-${service.id}`);
        if (menuBtn) {
            menuBtn.onclick = (e) => {
                e.stopPropagation();
                // Olası bir DOM yenilenmesine karşı güncel elementleri buluyoruz
                const currentDropdown = document.getElementById(`menu-${service.id}`);
                const currentBtn = document.getElementById(`btn-menu-${service.id}`);
                if (!currentDropdown || !currentBtn) return;
                
                const isHidden = currentDropdown.classList.contains('hidden');
                
                // Diğer tüm açık menüleri kapat
                document.querySelectorAll('.dropdown-menu').forEach(m => {
                    m.classList.add('hidden');
                    const b = document.querySelector(`[aria-controls="${m.id}"]`);
                    if (b) b.setAttribute('aria-expanded', 'false');
                });

                if (isHidden) {
                    currentDropdown.classList.remove('hidden');
                    currentBtn.setAttribute('aria-expanded', 'true');
                    
                    // Altta yer kalıp kalmadığını ölçüyoruz (Örn: Ekran yüksekliğini taşarsa)
                    const rect = currentDropdown.getBoundingClientRect();
                    const windowHeight = window.innerHeight;
                    if (rect.bottom > windowHeight) {
                        currentDropdown.classList.add('open-up');
                    } else {
                        currentDropdown.classList.remove('open-up');
                    }
                } else {
                    currentDropdown.classList.add('hidden');
                    currentBtn.setAttribute('aria-expanded', 'false');
                    currentDropdown.classList.remove('open-up');
                }
            };
        }

        const reinstallBtn = card.querySelector(`#btn-reinstall-${service.id}`);
        if (reinstallBtn) reinstallBtn.onclick = () => handlers.onReinstall(service.id, reinstallBtn);

        const removeBtn = card.querySelector(`#btn-remove-${service.id}`);
        if (removeBtn) removeBtn.onclick = () => handlers.onRemove(service.id, removeBtn);

        const delImageBtn = card.querySelector(`#btn-delete-image-${service.id}`);
        if (delImageBtn) delImageBtn.onclick = () => handlers.onDeleteImage(service.id, delImageBtn);
    }
}

export function toggleFormElements(card, isDisabled) {
    card.querySelectorAll('input, select, .tab').forEach(el => {
        if (el.getAttribute('data-type') === 'gpu_selector') {
            el.setAttribute('disabled', 'true');
            return;
        }
        const currentlyDisabled = el.hasAttribute('disabled') || (el.classList.contains('tab') && el.style.pointerEvents === 'none');

        if (isDisabled && !currentlyDisabled) {
            el.setAttribute('disabled', 'true');
            if (el.classList.contains('tab')) el.style.pointerEvents = 'none';
        } else if (!isDisabled && currentlyDisabled) {
            el.removeAttribute('disabled');
            if (el.classList.contains('tab')) el.style.pointerEvents = 'auto';
        }
    });
}

function renderEnvironmentSection(service, envOptions, isDisabled) {
    if (isCoreService(service)) return '';
    return `
        <div class="field">
            <label class="field-label" for="env-select-${service.id}">Donanim / Surucu</label>
            <select id="env-select-${service.id}" class="field-input" ${isDisabled ? 'disabled' : ''}>
                ${envOptions}
            </select>
        </div>
    `;
}

function renderModelSelectionSection(service, isDisabled) {
    if (isCoreService(service)) {
        return '';
    }

    let html = `
        <div class="field">
            <label class="field-label" for="model-select-${service.id}">Baslatilacak Model</label>
            <select id="model-select-${service.id}" class="field-input" ${isDisabled ? 'disabled' : ''}>
                <option value="">Model Secin...</option>
            </select>
        </div>
    `;

    if (service.category === 'llm' || service.category === 'embedding') {
        html += `
            <div id="mmproj-toggle-container-${service.id}" class="mmproj-container hidden">
                <!-- Otomatik buton buraya gelecek -->
            </div>
        `;
    }
    return html;
}

function setupCardInteractions(card, service, handlers) {
    card.querySelectorAll('.tab').forEach(tab => {
        tab.onclick = () => {
            card.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            card.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            const target = card.querySelector(`#${tab.dataset.tab}`);
            if (target) target.classList.add('active');
            if (tab.dataset.tab && tab.dataset.tab.startsWith('models-')) handlers.onTabModels(service.id);
        };
    });

    const modelSelect = card.querySelector(`#model-select-${service.id}`);
    if (modelSelect) {
        modelSelect.addEventListener('change', () => {
            handlers.onModelChange(service.id, modelSelect.value);
        });
    }

    // Donanım (CPU/GPU) seçimine göre GPU panelini göster/gizle
    const envSelect = card.querySelector(`#env-select-${service.id}`);
    const gpuField = card.querySelector(`#gpu-selector-field-${service.id}`);
    if (envSelect && gpuField) {
        const updateGpuVisibility = () => {
            const hw = (envSelect.options[envSelect.selectedIndex]?.getAttribute('data-hardware') || '').toLowerCase();
            if (hw === 'cpu') {
                gpuField.style.display = 'none';
            } else {
                gpuField.style.display = 'block';
            }
        };
        envSelect.addEventListener('change', updateGpuVisibility);
        updateGpuVisibility();
    }

    // Çoklu GPU'larda en az birinin seçili kalmasını zorunlu kıl
    const gpuCheckboxes = card.querySelectorAll(`.dynamic-input[data-type="gpu_selector_multi"]`);
    if (gpuCheckboxes.length > 0) {
        gpuCheckboxes.forEach(cb => {
            cb.addEventListener('change', () => {
                const checkedCount = Array.from(gpuCheckboxes).filter(c => c.checked).length;
                if (checkedCount === 0) {
                    cb.checked = true; // Geri al
                }
            });
        });
    }
}
