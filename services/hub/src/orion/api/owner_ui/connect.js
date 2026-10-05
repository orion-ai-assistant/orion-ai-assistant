/* This local owner page may be embedded; credentials never leave this origin. */
(() => {
    const node = id => document.getElementById(id);
    const root = location.pathname.startsWith('/panel/') ? '/panel' : '';
    let timer, source, busy = false, link = '';
    const requestId = new URLSearchParams(location.search).get('request_id');
    const reset = () => { clearInterval(timer); source?.close(); source = null; node('qr').removeAttribute('src'); link = '';  node('result').hidden = true; };
    async function api(path, body) {
        const response = await fetch(root + '/api/v1/owner' + path, {
            method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: body === undefined ? {} : {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)
        });
        const data = await response.json();
        if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : 'İşlem tamamlanamadı.'); error.status = response.status; throw error; }
        return data;
    }
    async function generate() {
        reset();
        const data = await api('/secure/pairing', requestId ? {request_id:requestId} : {});
        if (!data.qr_png || !data.link?.startsWith('https://')) throw new Error('QR oluşturulamadı.');
        node('qr').src = 'data:image/png;base64,' + data.qr_png;
        link = data.link;
        node('generate').hidden = true;
        if (data.pairing_id) {
            source = new EventSource(root + '/api/v1/owner/secure/pairing/events?pairing_id=' + encodeURIComponent(data.pairing_id) + (requestId ? '&request_id=' + encodeURIComponent(requestId) : ''));
            source.addEventListener('pairing_completed', () => { reset(); node('expiry').textContent = 'Cihaz eşleştirildi.'; });
        }
        node('result').hidden = false;
        const expires = Date.now() + Number(data.expires_in || 120) * 1000;
        const tick = () => {
            const remaining = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
            node('expiry').textContent = remaining ? `QR ${remaining} saniye geçerli.` : 'QR süresi doldu. Yeniden oluştur.';
            if (!remaining) { reset(); node('generate').hidden = false; }
        };
        tick(); timer = setInterval(tick, 1000);
    }
    async function perform(action) {
        if (busy) return;
        busy = true; node('error').hidden = true;
        const buttons = [...document.querySelectorAll('button')]; buttons.forEach(button => button.disabled = true);
        try { await action(); }
        catch (error) {
            node('error').textContent = error.message; node('error').hidden = false;
            if (error.status === 401) { reset(); node('unlock').hidden = false; node('generate').hidden = true; }
        } finally { busy = false; buttons.forEach(button => button.disabled = false); }
    }
    node('unlock').onsubmit = event => {
        event.preventDefault(); const password = node('password').value; node('password').value = '';
        perform(async () => { await api('/login', {password, scope:'admin'}); node('unlock').hidden = true; node('generate').hidden = false; await generate(); });
    };
    node('generate').onclick = () => perform(generate);
    node('copy').onclick = async () => { try { await navigator.clipboard.writeText(link); } catch { const input = document.createElement('textarea'); input.value = link; document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); } };
    window.addEventListener('pagehide', reset);
    perform(async () => {
        try { await api('/secure/addresses'); }
        catch (error) {
            if (error.status !== 401) throw error;
            const info = await api('/secure/password-info');
            if (info.default_password !== 'orion') throw error;
            await api('/login', {password:'orion', scope:'admin'});
            node('default').textContent = 'Şifre orion olduğu için QR otomatik açıldı. Hub ayarlarından yönetici şifreni değiştirebilirsin.';
            node('default').hidden = false;
        }
        await generate();
    });
})();
