(() => {
  const base = '/api/v1/owner/secure';
  const error = document.getElementById('secure-error');
  async function api(path, options = {}) {
    const response = await OwnerAPI.fetch(base + path, {credentials: 'same-origin', ...options});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Bağlantı kurulamadı.');
    return result;
  }
  async function run(action) {
    error.hidden = true;
    try { await action(); } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  }
  function row(container, name, detail, action, label) {
    const record = document.createElement('div'); record.className = 'record';
    const title = document.createElement('strong'); title.textContent = name;
    const description = document.createElement('p'); description.className = 'hint'; description.textContent = detail;
    record.append(title, description);
    if (action) { const button = document.createElement('button'); button.className = 'secondary'; button.textContent = label; button.onclick = () => run(action); record.append(button); }
    container.append(record);
  }
  async function refresh() {
    const [devices, routers, addresses] = await Promise.all([api('/devices'), api('/routers'), api('/addresses').catch(() => null)]);
    document.getElementById('local-browser-link').value = addresses?.local_browser_url || 'http://localhost/hub';
    document.getElementById('lan-browser-link').value = addresses?.lan_browser_url || '';
    const browserLink = document.getElementById('open-lan-browser');
    browserLink.hidden = !addresses;
    if (addresses) browserLink.href = addresses.lan_browser_url;
    else browserLink.removeAttribute('href');
    document.getElementById('copy-lan-browser').disabled = !addresses;
    if (!addresses) document.getElementById('address-copy-status').textContent = 'Ağ adresi bulunamadı. PC’nin ağ bağlantısını kontrol et.';
    const deviceList = document.getElementById('secure-devices'); deviceList.replaceChildren();
    for (const device of devices.devices) row(deviceList, device.name, device.id + (device.revoked ? ' · Erişim kaldırıldı' : ''), device.revoked ? null : async () => { await api('/devices/' + encodeURIComponent(device.id), {method: 'DELETE'}); await refresh(); }, 'Erişimi kaldır');
    const routerList = document.getElementById('secure-routers'); routerList.replaceChildren();
    for (const router of routers.routers) row(routerList, router.name, router.id + ' · ' + router.url + ' · SPKI: ' + router.fp, async () => { await api('/routers/' + encodeURIComponent(router.id), {method: 'DELETE'}); await refresh(); }, 'Güveni kaldır');
    if (!devices.devices.length) row(deviceList, 'Henüz eşleşmiş cihaz yok.', '', null);
    if (!routers.routers.length) row(routerList, 'Henüz güvenilen Router yok.', '', null);
  }
  let countdown;
  document.getElementById('create-pair').onclick = () => run(async () => {
    const pair = await api('/pairing', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({allow_registration: true})});
    document.getElementById('pair-result').hidden = false;
    // SVG originates exclusively from the locally authenticated QR generator.
    document.getElementById('pair-qr').innerHTML = pair.qr_svg;
    document.getElementById('pair-name').textContent = pair.qr.name;
    document.getElementById('pair-code').value = pair.qr.code;
    document.getElementById('pair-fp').value = pair.qr.fp;
    document.getElementById('pair-json').value = JSON.stringify(pair.qr, null, 2);
    document.getElementById('pair-link').value = pair.link;
    document.getElementById('pair-copy-status').textContent = '';
    clearInterval(countdown);
    const update = () => {
      const remaining = Math.max(0, Math.ceil(pair.expires_at - Date.now() / 1000));
      document.getElementById('pair-ttl').textContent = remaining ? remaining + ' saniye kaldı' : 'Kodun süresi doldu. Yeni bir QR oluştur.';
      if (!remaining) { clearInterval(countdown); document.getElementById('pair-qr').replaceChildren(); }
    };
    update(); countdown = setInterval(update, 1000);
  });
  function copyPair(fieldId) { return run(async () => {
    const field = document.getElementById(fieldId);
    const status = document.getElementById('pair-copy-status');
    field.focus(); field.select();
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(field.value);
      status.textContent = fieldId === 'pair-link' ? 'Bağlantı kopyalandı. Orion kurulu telefonda açılabilir.' : 'QR verisi kopyalandı.';
    } catch (_) {
      status.textContent = 'Metin seçildi. Ctrl+C veya cihazının kopyalama komutuyla kopyala.';
    }
  }); }
  document.getElementById('copy-pair-json').onclick = () => copyPair('pair-json');
  document.getElementById('copy-pair-link').onclick = () => copyPair('pair-link');
  document.getElementById('copy-lan-browser').onclick = () => run(async () => {
    const field = document.getElementById('lan-browser-link');
    const status = document.getElementById('address-copy-status');
    field.focus(); field.select();
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(field.value);
      status.textContent = 'Bağlantı kopyalandı. Diğer cihazın tarayıcısında açabilirsin.';
    } catch (_) {
      status.textContent = 'Metin seçildi. Ctrl+C veya cihazının kopyalama komutuyla kopyala.';
    }
  });
  document.getElementById('owner-password-form').onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {current_password: form.elements.current.value, new_password: form.elements.next.value};
    form.reset();
    run(async () => {
      await api('/password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      location.reload();
    });
  };
  document.getElementById('secure-refresh').onclick = () => run(refresh);
  document.addEventListener('owner-unlocked', () => run(refresh));
})();
