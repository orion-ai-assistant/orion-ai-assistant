export function renderParameters(service, isDisabled) {
    return Object.entries(service.parameters || {}).map(([id, rawValue]) => {
        const isObj = typeof rawValue === 'object' && rawValue !== null && !Array.isArray(rawValue);
        const defVal = isObj ? rawValue.default : rawValue;
        const label = isObj ? (rawValue.label || id) : id;

        if (typeof defVal === 'boolean') {
            const isChecked = defVal ? 'checked' : '';

            return `
                <div id="p-row-${service.id}-${id}" class="checkbox-row">
                    <input type="checkbox" id="p-${service.id}-${id}" ${isChecked} class="dynamic-input" data-param-id="${id}" data-type="checkbox" ${isDisabled ? 'disabled' : ''}>
                    <label for="p-${service.id}-${id}" class="checkbox-label">${label}</label>
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
