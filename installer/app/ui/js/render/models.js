import { formatSize } from '../ui-utils.js';

export function updateModelSelect(serviceId, models, allServiceModels) {
    const select = document.getElementById(`model-select-${serviceId}`);
    if (!select) return;

    const currentVal = select.value;
    let newHtml = `<option value="">${window.t('lbl_select_model')}</option>`;

    models.filter(m => m.is_installed && !(m.rel_path || "").toLowerCase().includes('mmproj')).forEach(m => {
        const path = m.rel_path || m.id;
        newHtml += `<option value="${path}" ${currentVal === path ? 'selected' : ''}>${m.name}</option>`;
    });

    if (select.innerHTML.trim() !== newHtml.trim()) {
        select.innerHTML = newHtml;
    }

    filterVisionModels(serviceId, select.value, allServiceModels);
}

export function filterVisionModels(serviceId, selectedModelPath, allServiceModels) {
    const container = document.getElementById(`mmproj-toggle-container-${serviceId}`);
    if (!container) return;

    if (!selectedModelPath) {
        container.classList.add('hidden');
        container.innerHTML = '';
        return;
    }

    const models = allServiceModels[serviceId] || [];

    // Klasör tespiti: Daha esnek bir yapı
    let targetFolder = selectedModelPath.includes('/')
        ? selectedModelPath.substring(0, selectedModelPath.lastIndexOf('/'))
        : selectedModelPath;

    targetFolder = targetFolder.replace(/\/+$/, "").toLowerCase();

    // Hedef klasör içindeki mmproj dosyasını bul
    const found = models.find(m => {
        const mPath = (m.rel_path || "").replace(/\\/g, "/");
        const mFolder = mPath.includes('/')
            ? mPath.substring(0, mPath.lastIndexOf('/')).toLowerCase()
            : '';

        // mmproj dosyası hedef klasörde mi?
        return m.is_installed &&
            mPath.toLowerCase().includes('mmproj') &&
            (mFolder === targetFolder || targetFolder === "");
    });

    if (found) {
        container.classList.remove('hidden');

        // Mevcut butonu kontrol et, eğer zaten varsa ve yolu aynıysa HTML'i bozma (state'i koru)
        const existingToggle = document.getElementById(`mmproj-toggle-${serviceId}`);
        if (existingToggle && existingToggle.dataset.path === found.rel_path) {
            return;
        }

        container.innerHTML = `
            <div class="mmproj-toggle-wrapper">
                <input type="checkbox" id="mmproj-toggle-${serviceId}" class="mmproj-checkbox" data-path="${found.rel_path}">
                <label for="mmproj-toggle-${serviceId}" class="mmproj-toggle-btn">
                    <i class="fas fa-eye"></i> ${window.t('ui_btn_vision')}
                </label>
            </div>
            <div class="mmproj-info">
                <i class="fas fa-info-circle"></i> ${window.t('ui_detected')}: <code>${found.name}</code>
            </div>
        `;
    } else {
        container.classList.add('hidden');
        container.innerHTML = '';
    }
}

export function renderModelList(serviceId, models, handlers, isDisabled = false) {
    const list = document.getElementById(`model-list-${serviceId}`);
    if (!list) return;

    const systemModels = models.filter(m => m.type !== 'local');
    const localModels = models.filter(m => m.type === 'local');

    const newHtml = [
        renderModelGroup(window.t('ui_system_models'), systemModels, isDisabled),
        renderModelGroup(window.t('ui_local_models'), localModels, isDisabled)
    ].filter(Boolean).join('');

    // Karşılaştırmayı daha esnek yapalım (boşluklar vs. sorun olmasın)
    const currentTrimmed = list.innerHTML.trim().replace(/\s+/g, ' ');
    const newTrimmed = newHtml.trim().replace(/\s+/g, ' ');

    if (currentTrimmed !== newTrimmed) {
        list.innerHTML = newHtml;

        // Sadece HTML değiştiğinde event listener'ları tekrar bağla
        models.forEach(m => {
            const btn = document.getElementById(`btn-dl-${m.id}`);
            if (btn) btn.onclick = () => handlers.onDownload(serviceId, m.id, btn);

            const delBtn = document.getElementById(`btn-del-${m.id}`);
            if (delBtn) delBtn.onclick = () => handlers.onDelete(serviceId, m.id, delBtn);
        });
    }
}

function renderModelGroup(title, groupModels, isDisabled) {
    if (!groupModels.length) return '';
    const itemsHtml = groupModels.map(m => renderModelItem(m, isDisabled)).join('');

    return `
        <div class="model-group">
            <div class="model-group-title">${title}</div>
            ${itemsHtml}
        </div>
    `;
}

function renderModelItem(m, isDisabled) {
    let statusHtml = '';
    let btnHtml = '';
    const currentSize = m.size_mb > 0 ? formatSize(m.size_mb) : '';
    const totalSize = m.total_expected_mb > 0 ? formatSize(m.total_expected_mb) : '';

    if (m.is_downloading) {
        const progressText = m.download_progress ? `(${m.download_progress})` : '';
        const sizeText = totalSize ? `${currentSize} / ${totalSize}` : currentSize;
        statusHtml = `<span class="model-status downloading">${window.t('ui_downloading')} ${progressText} ${sizeText}</span>`;
        btnHtml = '<button class="btn btn-primary btn-small" disabled><i class="fas fa-spinner fa-spin"></i></button>';
    } else if (m.is_installed) {
        statusHtml = `<span class="model-status installed">${currentSize}</span>`;
        btnHtml = `<button class="btn btn-danger btn-small" id="btn-del-${m.id}" ${isDisabled ? 'disabled' : ''} title="${window.t('btn_remove')}"><i class="fas fa-trash"></i></button>`;
    } else {
        const incompleteText = m.incomplete_status ? ` (${m.incomplete_status})` : '';
        const manifestSizeText = m.manifest_size_mb > 0 ? ` (${formatSize(m.manifest_size_mb)})` : '';
        statusHtml = m.is_incomplete
            ? `<span class="model-status warning">${window.t('ui_incomplete')}${incompleteText}</span>`
            : `<span class="model-status missing">${window.t('ui_missing')}${manifestSizeText}</span>`;
        btnHtml = `<button class="btn btn-primary btn-small" id="btn-dl-${m.id}" ${isDisabled ? 'disabled' : ''} title="${m.is_incomplete ? window.t('ui_resume') : window.t('ui_download')}">${m.is_incomplete ? '<i class="fas fa-play"></i>' : '<i class="fas fa-download"></i>'}</button>`;
    }

    return `
        <div class="model-item ${isDisabled ? 'is-disabled' : ''}">
            <div class="model-info">
                <span class="model-name">${m.name}</span>
                ${statusHtml}
            </div>
            <div class="model-actions">${btnHtml}</div>
        </div>`;
}
