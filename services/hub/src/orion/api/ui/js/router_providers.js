/* Personal credentials are write-only; Router selection belongs to Hub settings. */
window.RouterProviders = {
    clearInput() { const input = document.querySelector('#router-provider-card input'); if (input) input.value = ''; },
    reset() { this.clearInput(); document.getElementById('router-provider-card')?.remove(); document.getElementById('hub-panel-card')?.remove(); },
    mount() {
        const host = document.getElementById('settings-dashboard');
        if (!host) return;
        const context = () => Auth.tokenKey + ':' + Auth.getToken() + ':' + OrionI18n.language;
        const existing = document.getElementById('router-provider-card');
        if (existing?._accountContext === context()) return existing._refresh?.();
        if (existing) this.reset();
        const en = OrionI18n.language === 'en';
        const text = (tr, english) => en ? english : tr;

        const card = document.createElement('section');
        card.id = 'router-provider-card'; card.className = 'settings-card router-provider-card';
        card._accountContext = context();
        card.innerHTML = `<div class="settings-card-header"><div class="settings-card-icon">◇</div><h3>Orion Router</h3><div data-router-link></div></div>
          <div class="settings-card-body"><div class="router-identity"><strong data-name></strong><span class="router-state" data-state></span><span class="router-count" data-count></span></div><p role="status" data-status></p>
          <details class="router-disclosure" data-details><summary>${text('Sağlayıcı anahtarları','Provider keys')}</summary><div class="router-provider-list" data-configured></div></details>
          <details class="router-disclosure" data-personal><summary>${text('Kişisel anahtar ekle','Add personal key')}</summary><div class="router-personal-body">
          <p class="router-hint">${text('Sağlayıcı anahtarları hesabınıza özeldir ve Orion Router’da saklanır.','Provider keys belong to your account and are stored in Orion Router.')}</p>
          <form hidden><div class="setting-row"><label class="setting-label" for="personal-provider">${text('Sağlayıcı','Provider')}</label><div class="setting-control"><select id="personal-provider" class="setting-select" required></select></div></div>
          <div class="setting-row"><label class="setting-label" for="personal-provider-key">${text('Sağlayıcı API anahtarı','Provider API key')}</label><div class="setting-control"><input id="personal-provider-key" class="setting-input" type="password" autocomplete="off" spellcheck="false" maxlength="8192" required></div></div>
          <button class="btn btn-primary" type="submit">${text('Sağlayıcı anahtarını kaydet','Save provider key')}</button></form><div data-saved></div></div></details>
          <p role="alert" data-message></p></div>`;
        const hub = document.createElement('section'); hub.id='hub-panel-card'; hub.className='settings-card';
        hub.innerHTML=`<div class="settings-card-header"><div class="settings-card-icon">▣</div><div class="router-hub-heading"><h3>Orion Hub Panel</h3><p class="router-hint">${text('Yalnızca Hub’ın çalıştığı cihazda açılır.','Opens only on the device running the Hub.')}</p></div><div data-hub-panel></div></div>`;
        host.before(card,hub);
        const form = card.querySelector('form'), selector=form.querySelector('select'), input=form.querySelector('input'), message=card.querySelector('[data-message]');
        const initialContext = context();
        let routerId, snapshot, loading=false;
        const valid = () => card.isConnected && context() === initialContext;
        const request = async (path, options = {}) => {
            const response = await API._fetch('/api/v1/router/providers' + path, options);
            const data = await response.json();
            if (!valid()) throw new Error(text('Hub oturumu değişti.','Hub session changed.'));
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : text('İşlem tamamlanamadı.','Operation failed.'));
            return data;
        };
        const address = (container, value, label, routerAddress = false) => {
            let url; try { url=new URL(value); } catch {return;}
            if (!['http:','https:'].includes(url.protocol) || url.username || url.password) return;
            if(routerAddress && ['localhost','127.0.0.1','[::1]'].includes(url.hostname)) {const hint=document.createElement('p');hint.textContent=text('Router bu cihazda çalışıyor','Router runs on this device');container.append(hint);return;}
            const line=document.createElement('div'); line.className='router-address';
            const link=document.createElement('a'); link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.textContent=label || url.host;
            line.append(link);container.append(line);
        };
        const load = async () => {
            if(loading)return;loading=true;let data;try{data=await request('');}finally{loading=false;}
            const next=JSON.stringify(data);if(next===snapshot)return;snapshot=next;
            const routers=data.routers || [data];if(routerId!==data.router_id)input.value='';routerId=data.router_id;
            card.querySelector('[data-name]').textContent=data.router_name || 'Orion Router';
            card.querySelector('[data-state]').textContent=text(data.connected?'Bağlı':'Bağlı değil',data.connected?'Connected':'Disconnected');
            card.querySelector('[data-count]').textContent=`${data.active_keys || 0} ${text('aktif anahtar','active keys')}`;
            card.querySelector('[data-status]').textContent=data.connected?'':data.message || '';
            const links=card.querySelector('[data-router-link]');links.replaceChildren();
            address(links,data.dashboard_url,text('Router yönetimini aç','Open Router management'),true);
            if (data.managed) {const row=document.getElementById('setting-input-router_api_key')?.closest('.setting-row');if(row){row.hidden=true;row.style.display='none';}}
            form.hidden=!data.connected;card.querySelector('[data-personal]').hidden=!data.connected;
            const previousProvider=selector.value;selector.replaceChildren(...(data.providers || []).filter(p=>p!=='local').map(p=>new Option(p,p)));if([...selector.options].some(option=>option.value===previousProvider))selector.value=previousProvider;
            form.querySelector('button').disabled=!selector.options.length;
            const configured=card.querySelector('[data-configured]');configured.replaceChildren();
            for (const info of routers) {
                if (info.router_id!==routerId) {const heading=document.createElement('h4');heading.textContent=info.router_name;configured.append(heading);address(configured,info.dashboard_url,null,true);}
                for (const count of info.summary || []) {
                    const row=document.createElement('div');row.className='router-provider-row';
                    const name=document.createElement('strong');name.textContent=count.provider;
                    const counts=document.createElement('span');counts.className='router-provider-counts';
                    for (const [n,label] of [[count.shared,text('ortak','shared')],[count.private,text('size özel','private')]]) {const badge=document.createElement('span');badge.className='router-badge';badge.textContent=`${n} ${label}`;counts.append(badge);}
                    row.append(name,counts);configured.append(row);
                }
                for (const item of info.personal_keys || []) {const row=document.createElement('div');row.className='router-key-state';row.textContent=`${item.provider} · ${text(item.is_active?'Aktif':'Yönetici tarafından kapatıldı',item.is_active?'Active':'Disabled by administrator')}${item.admin_updated?' · '+text('Yönetici tarafından güncellendi','Updated by administrator'):''}`;configured.append(row);}
            }
            if(!configured.children.length) configured.textContent=text('Henüz sağlayıcı anahtarı yok.','No provider keys yet.');
            const saved=card.querySelector('[data-saved]');saved.replaceChildren();
            for (const provider of data.configured || []) {
                const row=document.createElement('div');row.className='router-provider-row';const name=document.createElement('span');name.className='router-saved-key';const providerName=document.createElement('strong');providerName.textContent=provider;const mask=document.createElement('code');mask.textContent=(data.personal_keys || []).find(k=>k.provider===provider)?.masked_key || '••••';name.append(providerName,mask);
                const remove=document.createElement('button');remove.type='button';remove.className='btn btn-secondary';remove.textContent=text('Kaldır','Remove');
                remove.onclick=async()=>{remove.disabled=true;message.textContent='';const targetRouter=routerId;try {await request('/'+encodeURIComponent(provider)+'?router_id='+encodeURIComponent(targetRouter),{method:'DELETE'});await load();}catch(error){if(valid())message.textContent=error.message;}finally{remove.disabled=false;}};
                row.append(name,remove);saved.append(row);
            }
        };
        form.onsubmit=async event=>{
            event.preventDefault();const key=input.value.trim();input.value='';if(!key || !valid())return;
            const button=form.querySelector('button');button.disabled=true;message.textContent='';
            try {await request('/'+encodeURIComponent(selector.value),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key,router_id:routerId})});await load();message.textContent=text('Anahtar kaydedildi. Router’ın model listesinden model seçebilirsiniz.','Key saved. Select a model from the Router catalog.');UI.loadModelChoices?.(UI.currentSettings,true)?.catch(()=>{});}
            catch(error){if(valid())message.textContent=error.message;}finally{if(valid())button.disabled=!selector.options.length;}
        };
        card._refresh=()=>load().catch(error=>{if(valid())message.textContent=error.message;});
        API._fetch('/api/v1/pair/capabilities').then(r=>r.ok?r.json():{}).then(data=>{
            if(!valid())return;const content=hub.querySelector('[data-hub-panel]');
            const url=data.can_manage_local && data.management_url;
            if(url && ['localhost','127.0.0.1','[::1]'].includes(location.hostname)) address(content,url,text('Hub yönetimini aç','Open Hub management'));
            else content.textContent=text('Yönetim panelini Hub’ın çalıştığı bilgisayardan açabilirsiniz.','Open the management panel on the computer running the Hub.');
        }).catch(()=>{if(valid())hub.querySelector('[data-hub-panel]').textContent=text('Hub paneli bilgileri alınamadı.','Hub panel information unavailable.');});
        load().catch(error=>{if(valid())message.textContent=error.message;});
    },
};
