(() => {
    const form = document.getElementById('storage-form');
    const result = document.getElementById('storage-result');
    const move = document.getElementById('storage-move');
    const refresh = document.getElementById('storage-refresh');
    let revision = null;
    let pending = false;
    const text = key => window.t(key);
    async function read() {
        move.disabled = true;
        revision = null;
        const response = await fetch('/api/installation/locations', { cache: 'no-store' });
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || text('storage_error'));
        document.getElementById('storage-current').textContent = Object.entries(value.locations)
            .map(([name, location]) => `${text('storage_' + name)}: ${location.path}${location.available ? '' : ' (' + text('storage_missing') + ')'}`)
            .join('\n') || text('storage_unregistered');
        revision = value.revision;
        move.disabled = !revision || pending;
    }
    refresh.addEventListener('click', () => read().catch(error => { result.textContent = error.message; }));
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!revision || pending) return;
        pending = true;
        move.disabled = true;
        refresh.disabled = true;
        result.textContent = text('storage_working');
        try {
            const response = await fetch('/api/installation/locations/move', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: document.getElementById('storage-kind').value,
                    destination: document.getElementById('storage-destination').value.trim(), expected_revision: revision })
            });
            const value = await response.json();
            if (!response.ok) throw new Error(value.detail || text('storage_error'));
            result.textContent = text('storage_done');
        } catch (error) { result.textContent = error.message; }
        finally {
            pending = false;
            refresh.disabled = false;
            await read().catch(error => { result.textContent = error.message; });
        }
    });
    read().catch(error => { result.textContent = error.message; });
})();
