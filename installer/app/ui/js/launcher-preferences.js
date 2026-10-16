(() => {
    const form = document.getElementById('launcher-form');
    const startup = document.getElementById('launcher-startup');
    const windowOption = document.getElementById('launcher-window');
    const save = document.getElementById('launcher-save');
    const result = document.getElementById('launcher-result');
    let pending = false;
    async function read() {
        const response = await fetch('/api/installation/launcher', { cache: 'no-store' });
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail);
        const available = value.registered && value.windows;
        startup.disabled = windowOption.disabled = save.disabled = !available || pending;
        if (value.preferences) {
            startup.checked = value.preferences.start_with_windows;
            windowOption.checked = value.preferences.show_client;
        }
        if (!available) result.textContent = window.t('launcher_unavailable');
    }
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (pending || save.disabled) return;
        pending = true;
        startup.disabled = windowOption.disabled = save.disabled = true;
        try {
            const response = await fetch('/api/installation/launcher', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ start_with_windows: startup.checked, show_client: windowOption.checked })
            });
            const value = await response.json();
            if (!response.ok) throw new Error(value.detail);
            result.textContent = window.t('launcher_saved');
        } catch (error) { result.textContent = error.message; }
        finally { pending = false; await read().catch(error => { result.textContent = error.message; }); }
    });
    read().catch(error => { result.textContent = error.message; });
})();
