export function renderParameters(service, isDisabled) {
    return Object.entries(service.parameters || {}).map(([id, rawValue]) => {
        const isObj = typeof rawValue === 'object' && rawValue !== null && !Array.isArray(rawValue);
        const defVal = isObj ? rawValue.default : rawValue;
        const genericLabelKey = `param_${id}`.toLowerCase();
        const serviceLabelKey = `param_${service.id.replace(/-/g, '_')}_${id}`.toLowerCase();

        let label = isObj ? (rawValue.label || id) : id;
        if (window.t(serviceLabelKey) !== serviceLabelKey) {
            label = window.t(serviceLabelKey);
        } else if (window.t(genericLabelKey) !== genericLabelKey) {
            label = window.t(genericLabelKey);
        }

        if (typeof defVal === 'boolean') {
            const isChecked = defVal ? 'checked' : '';

            return `
                <div id="p-row-${service.id}-${id}" class="checkbox-row">
                    <input type="checkbox" id="p-${service.id}-${id}" ${isChecked} class="dynamic-input" data-param-id="${id}" data-type="checkbox" ${isDisabled ? 'disabled' : ''}>
                    <label for="p-${service.id}-${id}" class="checkbox-label">${label}</label>
                </div>`;
        }

        if (isObj && rawValue.type === 'gpu_selector') {
            const gpus = window.orionGpuList || [];
            
            let gpuContent = '';
            if (gpus.length <= 1) {
                // Sadece 1 GPU varsa veya bilinmiyorsa, disabled checked goster. API'de 'all' kullanilacak
                const gpuName = gpus.length === 1 ? gpus[0].name : window.t('lbl_default_gpu');
                gpuContent = `
                    <div class="checkbox-row" style="opacity: 0.7;">
                        <input type="checkbox" id="p-${service.id}-${id}-default" checked disabled class="dynamic-input" data-param-id="${id}" data-type="gpu_selector" value="all">
                        <label for="p-${service.id}-${id}-default" class="checkbox-label" style="font-size: 13px;">[GPU 0] ${gpuName}</label>
                    </div>`;
            } else {
                // Birden fazla GPU varsa mini kutucuklar listesi
                const checkboxes = gpus.map(gpu => {
                    return `
                        <div class="checkbox-row" style="margin-bottom: 4px;">
                            <input type="checkbox" id="p-${service.id}-${id}-${gpu.id}" checked class="dynamic-input" data-param-id="${id}" data-type="gpu_selector_multi" value="${gpu.id}" ${isDisabled ? 'disabled' : ''}>
                            <label for="p-${service.id}-${id}-${gpu.id}" class="checkbox-label" style="font-size: 13px;">[GPU ${gpu.id}] ${gpu.name} (${gpu.vram}MB)</label>
                        </div>`;
                }).join('');
                
                gpuContent = `
                    <div class="gpu-list" style="margin-top: 5px; padding: 10px; background: rgba(0,0,0,0.1); border-radius: 6px; border: 1px solid var(--border-color);">
                        ${checkboxes}
                    </div>`;
            }

            return `
                <div class="field gpu-selector-field" id="gpu-selector-field-${service.id}">
                    <label class="field-label">${label}</label>
                    ${gpuContent}
                </div>`;
        }

        if (typeof defVal === 'number' || (isObj && rawValue.type === 'number') || (isObj && rawValue.type === 'int')) {
            const minVal = isObj && rawValue.min !== undefined ? rawValue.min : 0; // Default to 0 to prevent negative
            const maxVal = isObj && rawValue.max !== undefined ? rawValue.max : undefined;
            const isInt = (isObj && rawValue.type === 'int') || Number.isInteger(defVal);
            const stepVal = isObj && rawValue.step !== undefined ? rawValue.step : (isInt ? 1 : 'any');
            
            const minAttr = minVal !== undefined ? `min="${minVal}"` : '';
            const maxAttr = maxVal !== undefined ? `max="${maxVal}"` : '';
            const stepAttr = stepVal !== undefined ? `step="${stepVal}"` : '';

            return `
                <div class="field">
                    <label class="field-label" for="p-${service.id}-${id}">${label}</label>
                    <input type="number" id="p-${service.id}-${id}" value="${defVal}" ${minAttr} ${maxAttr} ${stepAttr}
                        class="dynamic-input field-input" data-param-id="${id}" data-type="${isInt ? 'int' : 'number'}" ${isDisabled ? 'disabled' : ''}>
                </div>`;
        }

        return `
            <div class="field">
                <label class="field-label" for="p-${service.id}-${id}">${label}</label>
                <input type="text" id="p-${service.id}-${id}" value="${defVal}" class="dynamic-input field-input" data-param-id="${id}" data-type="text" ${isDisabled ? 'disabled' : ''}>
            </div>`;
    }).join('');
}
