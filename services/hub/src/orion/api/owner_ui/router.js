document.addEventListener('DOMContentLoaded', () => {
    const current = document.getElementById('router-current');
    const error = document.getElementById('router-error');
    const accessStatus = document.getElementById('router-access-status');
    const unfinished = document.getElementById('router-unfinished');
    const list = document.getElementById('router-list');
    const refresh = document.getElementById('router-refresh');
    const manual = document.getElementById('router-manual');
    const wizard = document.getElementById('router-wizard');
    const wizardError = document.getElementById('router-wizard-error');
    const codeInput = document.getElementById('router-code');
    let currentRouterId;
    let discoveredRouters = null;
    let target = null;

    async function request(path, options) {
        const response = await OwnerAPI.fetch('/api/v1/owner/router' + path, options);
        const data = await response.json();
        if (!response.ok) {
            const failure = new Error(typeof data.detail === 'string' ? data.detail : 'Router bağlantısı kontrol edilemedi.');
            failure.status = response.status;
            throw failure;
        }
        return data;
    }
    const post = (path, body) => request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

    const show = selected => {
        if (!selected) { current.className = ''; current.textContent = 'Bu Hub’ın model bağlantısı henüz kurulmamış. Aşağıdan bir Router seçerek bağlan.'; return; }
        current.replaceChildren();
        current.className = 'connection-status';
        const dot = document.createElement('span'); dot.className = 'status-dot';
        const text = document.createElement('span');
        const name = document.createElement('span'); name.className = 'router-current-name'; name.textContent = selected.name;
        const address = document.createElement('span'); address.className = 'router-current-url'; address.textContent = selected.url || 'Bağlantı bekleniyor';
        text.append(name, address); current.append(dot, text); current.title = selected.id || '';
    };
    async function loadStatus(background = false) {
        // A background check reads the saved selection only; it never scans the network.
        const data = await request(background ? '?light=true' : '', {background});
        show(data.selected);
        currentRouterId = data.selected?.id;
        if (discoveredRouters !== null) renderRouters();
        const waiting = Boolean(data.selected) && !data.access_ready;
        accessStatus.textContent = waiting ? '' : data.message;
        unfinished.hidden = !waiting;
        document.getElementById('router-unfinished-text').textContent = data.message;
        document.getElementById('router-finish').hidden = !data.needs_code;
        return data;
    }
    async function operation(work) {
        refresh.disabled = true;
        error.hidden = true;
        try { await work(); }
        catch (failure) { error.textContent = failure.message; error.hidden = false; }
        finally { refresh.disabled = false; }
    }

    // On this PC, or for a Router this Hub already knows, connecting needs nothing; otherwise one dashboard code.
    function step(name) {
        for (const panel of wizard.querySelectorAll('[data-panel]')) panel.hidden = panel.dataset.panel !== name;
        const order = ['code', 'done'];
        const reached = name === 'working' ? 0 : order.indexOf(name);
        wizard.querySelectorAll('.wizard-steps li').forEach((item, index) => {
            item.classList.toggle('done', index < reached);
            if (index === reached) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
        });
        if (name === 'code') codeInput.focus();
    }
    function fail(message) { wizardError.textContent = message; wizardError.hidden = false; }
    function advance(result) {
        currentRouterId = result.selected?.id || currentRouterId;
        if (result.access_ready) {
            document.getElementById('router-wizard-done').textContent = `${result.selected?.name || target.name} bağlandı. Kullanıcı hesapları kendiliğinden oluşturulur.`;
            step('done');
        } else {
            if (!result.needs_code) fail(result.message);
            step('code');
        }
    }
    async function connect(code) {
        wizardError.hidden = true;
        try {
            advance(await post('', {...target.selection, ...(code ? {code} : {})}));
        } catch (failure) {
            // 400 without a code: this Router is new to the Hub, so its connection code is the next step.
            if (code || failure.status !== 400) fail(failure.message);
            step('code');
        }
    }
    function openWizard(next, startAt) {
        target = next;
        wizardError.hidden = true;
        codeInput.value = '';
        document.getElementById('router-wizard-target').textContent = [next.name, next.url].filter(Boolean).join(' · ');
        wizard.showModal();
        if (startAt === 'code') { step('code'); return; }
        step('working');
        connect();
    }
    document.getElementById('router-wizard-code').onsubmit = async event => {
        event.preventDefault();
        const button = event.target.querySelector('button');
        button.disabled = true;
        try { await connect(codeInput.value.trim()); }
        finally { button.disabled = false; }
    };
    const closeWizard = () => wizard.close();
    document.getElementById('router-wizard-close').onclick = closeWizard;
    document.getElementById('router-wizard-finish').onclick = closeWizard;
    wizard.addEventListener('close', () => { operation(loadStatus); if (discoveredRouters !== null) refresh.click(); });

    function renderRouters() {
        list.replaceChildren();
        const available = discoveredRouters.filter(router => router.id !== currentRouterId);
        for (const router of available) {
            const card = document.createElement('article'); card.className = 'router-card';
            const details = document.createElement('div'); details.className = 'router-details';
            const title = document.createElement('h3'); title.textContent = router.name;
            const address = document.createElement('p'); address.textContent = router.url;
            const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = router.same_pc ? 'Bu bilgisayar · otomatik bağlanır' : 'Ağda bulundu';
            details.append(title, address, badge);
            const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Bağlan';
            button.disabled = Boolean(router.conflict);
            button.onclick = () => openWizard({selection: {id: router.id}, name: router.name, url: router.url});
            card.append(details, button); list.append(card);
        }
        if (!available.length) list.textContent = currentRouterId
            ? 'Bağlı Router dışında başka Router bulunamadı.'
            : 'Router bulunamadı. Aynı ağda açık olduğundan emin ol veya elle adres yaz.';
    }
    refresh.onclick = () => operation(async () => {
        await loadStatus();
        const data = await request('/discover');
        discoveredRouters = data.routers;
        renderRouters();
    });
    manual.onsubmit = event => {
        event.preventDefault();
        const url = document.getElementById('router-address').value.trim();
        openWizard({selection: {manual_url: url}, name: 'Orion Router', url});
    };
    document.getElementById('router-finish').onclick = () => openWizard({selection: {id: currentRouterId}, name: current.querySelector('.router-current-name')?.textContent || 'Router', url: ''}, 'code');
    document.addEventListener('owner-unlocked', async () => {
        await operation(loadStatus);
        refresh.click();
    });
    // The Router's administrator can deactivate or remove this Hub at any time.
    OwnerAPI.whileVisible(() => loadStatus(true));
});
