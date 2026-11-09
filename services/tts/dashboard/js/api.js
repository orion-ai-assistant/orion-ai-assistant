const API_BASE = "";

export async function getModelInfo() {
    const res = await fetch(`${API_BASE}/v1/model_info`);
    return await res.json();
}

export async function getLanguages() {
    const res = await fetch(`${API_BASE}/v1/languages`);
    return await res.json();
}

export async function getDesignOptions() {
    const res = await fetch(`${API_BASE}/v1/design_options`);
    return await res.json();
}

export async function getAudioSettings() {
    const res = await fetch(`${API_BASE}/v1/settings/audio`);
    if (!res.ok) throw new Error('Ses ayarları okunamadı');
    return await res.json();
}

export async function setAudioSettings(settings) {
    const res = await fetch(`${API_BASE}/v1/settings/audio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Ses ayarı uygulanamadı');
    }
    return await res.json();
}

export async function savePrime(settings) {
    return await fetch(`${API_BASE}/v1/prime`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    });
}

export async function getVoices() {
    const res = await fetch(`${API_BASE}/v1/voices`);
    return await res.json();
}

export async function deleteVoice(name) {
    const res = await fetch(`${API_BASE}/v1/voices/${name}`, { method: 'DELETE' });
    return res.ok;
}

export async function cloneVoice(formData) {
    const res = await fetch(`${API_BASE}/v1/voices/clone`, {
        method: 'POST',
        body: formData
    });
    return res;
}

export async function generateSpeech(payload) {
    const res = await fetch(`${API_BASE}/v1/audio/speech`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    return res;
}
