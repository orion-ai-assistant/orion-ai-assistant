(() => {
    const dialog = document.getElementById('owner-password-dialog');
    const form = document.getElementById('owner-password-form');
    const error = document.getElementById('owner-password-error');
    const submit = form.querySelector('button[type="submit"]');
    document.getElementById('owner-password-open').onclick = () => {
        if (!OwnerGate.unlocked) return;
        form.reset(); error.hidden = true; dialog.showModal();
        form.elements.current.focus();
    };
    document.getElementById('owner-password-close').onclick = () => dialog.close();
    dialog.addEventListener('close', () => { form.reset(); error.hidden = true; });
    form.onsubmit = async event => {
        event.preventDefault();
        if (submit.disabled || !OwnerGate.unlocked) return;
        const payload = {current_password: form.elements.current.value, new_password: form.elements.next.value};
        form.reset(); submit.disabled = true; error.hidden = true;
        try {
            const response = await OwnerAPI.fetch('/api/v1/owner/secure/password', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Şifre değiştirilemedi.');
            dialog.close(); OwnerGate.lock();
        } catch (failure) {
            error.textContent = failure.message; error.hidden = false;
        } finally { submit.disabled = false; }
    };
})();
