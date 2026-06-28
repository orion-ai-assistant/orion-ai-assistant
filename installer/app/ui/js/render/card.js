import { renderParameters } from './parameters.js';

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
    const envOptions = (service.supported_environments || []).map(env =>
        `<option value="${env.id}" data-hardware="${env.hardware || ''}">${env.name}</option>`
    ).join('');

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
        <div class="category-tag">${service.category.toUpperCase()}</div>
        <div class="service-header">
            <h3 class="service-name">${service.name}</h3>
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

    const hasInstall = service.is_installed;
    const statusLabel = !hasInstall ? 'Kurulmamis' : (service.is_running ? 'Calisiyor' : 'Durduruldu');
    const statusClass = !hasInstall ? 'status-missing' : (service.is_running ? 'status-running' : 'status-stopped');

    let actionHtml = '';
    const hasContainer = !!service.is_installed;

    if (isDisabled) {
        actionHtml = '<button class="btn" disabled>Kullanilamaz</button>';
    } else if (service.is_installing) {
        actionHtml = '<button class="btn btn-primary" disabled><i class="fas fa-spinner fa-spin"></i> Kuruluyor...</button>';
    } else {
        const mainBtnClass = service.is_running ? 'btn btn-danger' : 'btn btn-primary';
        const mainBtnLabel = !hasContainer ? 'Kur' : (service.is_running ? 'Durdur' : 'Yeniden Kur');
        const mainBtnId = `btn-main-${service.id}`;
        const menuBtnId = `btn-menu-${service.id}`;
        const dropdownId = `menu-${service.id}`;
        const showDropdown = service.is_running;
        const dropdownHtml = showDropdown ? `
            <div class="split-dropdown">
                <button class="btn btn-split-toggle" id="${menuBtnId}" aria-expanded="false" aria-controls="${dropdownId}" title="Diger islemler">
                    <i class="fas fa-chevron-down"></i>
                </button>
                <div class="dropdown-menu hidden" id="${dropdownId}">
                    <button class="dropdown-item danger" id="btn-remove-${service.id}">Sil</button>
                </div>
            </div>
        ` : '';
        actionHtml = `
            <div class="split-action"><button class="${mainBtnClass}" id="${mainBtnId}">${mainBtnLabel}</button>${dropdownHtml}</div>
        `;
    }

    const newFooterHtml = `
        <div class="service-footer">
            <div class="status-badge ${statusClass}"><span class="status-dot"></span> ${statusLabel}</div>
            <div class="footer-actions">${actionHtml}</div>
        </div>
    `;

    if (footer.innerHTML !== newFooterHtml) {
        footer.innerHTML = newFooterHtml;

        // Re-bind events ONLY if HTML changed
        const mainBtn = card.querySelector(`#btn-main-${service.id}`);
        if (mainBtn) {
            if (service.is_running) {
                mainBtn.onclick = () => handlers.onStop(service.id, mainBtn);
            } else if (hasContainer) {
                mainBtn.onclick = () => handlers.onReinstall(service.id, mainBtn);
            } else {
                mainBtn.onclick = () => handlers.onStart(service.id, mainBtn);
            }
        }

        const menuBtn = card.querySelector(`#btn-menu-${service.id}`);
        const dropdown = card.querySelector(`#menu-${service.id}`);
        if (menuBtn && dropdown) {
            menuBtn.onclick = (e) => {
                e.stopPropagation();
                const isHidden = dropdown.classList.contains('hidden');
                card.querySelectorAll('.dropdown-menu').forEach(m => m.classList.add('hidden'));
                dropdown.classList.toggle('hidden', !isHidden);
                menuBtn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
            };
            document.addEventListener('click', () => {
                dropdown.classList.add('hidden');
                menuBtn.setAttribute('aria-expanded', 'false');
            }, { once: true });
        }

        const removeBtn = card.querySelector(`#btn-remove-${service.id}`);
        if (removeBtn) removeBtn.onclick = () => handlers.onRemove(service.id, removeBtn);
    }
}

export function toggleFormElements(card, isDisabled) {
    card.querySelectorAll('input, select, .tab').forEach(el => {
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
}
