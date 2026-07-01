const API_BASE = '/api';

export async function fetchHardware() {
    const response = await fetch(`${API_BASE}/hardware`);
    return await response.json();
}

export async function fetchServices() {
    const response = await fetch(`${API_BASE}/services`);
    if (!response.ok) throw new Error("Server error");
    return await response.json();
}

export async function fetchModels(serviceId) {
    const response = await fetch(`${API_BASE}/services/${serviceId}/models/check`);
    return await response.json();
}

export async function postInstallService(id, query) {
    const response = await fetch(`${API_BASE}/services/${id}/install?${query}`, { method: 'POST' });
    return await response.json();
}

export async function postStopService(id) {
    const response = await fetch(`${API_BASE}/services/${id}/stop`, { method: 'POST' });
    return await response.json();
}

export async function postRemoveService(id) {
    const response = await fetch(`${API_BASE}/services/${id}/remove`, { method: 'POST' });
    return await response.json();
}

export async function postRemoveImage(id) {
    const response = await fetch(`${API_BASE}/services/${id}/remove-image`, { method: 'POST' });
    return await response.json();
}

export async function postToggleAutostart(id) {
    const response = await fetch(`${API_BASE}/services/${id}/autostart`, { method: 'POST' });
    return await response.json();
}

export async function postStartSystem() {
    const response = await fetch(`${API_BASE}/system/start`, { method: 'POST' });
    return await response.json();
}

export async function postDownloadModel(serviceId, modelId) {
    const response = await fetch(`${API_BASE}/services/${serviceId}/models/download?model_id=${modelId}`, { method: 'POST' });
    return await response.json();
}

export async function postDeleteModel(serviceId, modelId) {
    const response = await fetch(`${API_BASE}/services/${serviceId}/models/delete?model_id=${modelId}`, { method: 'POST' });
    return await response.json();
}

export async function postKeepAlive() {
    try {
        const response = await fetch(`${API_BASE}/keepalive`, { method: 'POST' });
        return await response.json();
    } catch (err) {
        console.error("Keepalive failed:", err);
    }
}

export function sendShutdownBeacon() {
    if (navigator.sendBeacon) {
        navigator.sendBeacon(`${API_BASE}/shutdown`);
    } else {
        fetch(`${API_BASE}/shutdown`, { method: 'POST', keepalive: true }).catch(() => {});
    }
}
