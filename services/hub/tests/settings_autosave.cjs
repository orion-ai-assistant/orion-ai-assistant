const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const timers = new Map();
let timerId = 0;
const elements = {};
function field(key, value, tagName = 'INPUT') {
    const classes = new Set();
    const input = {value, tagName, dataset: {original: value}, validity: {valid: true},
        classList: {add: c => classes.add(c), contains: c => classes.has(c), toggle(c, on) { if (on) classes.add(c); else classes.delete(c); }}};
    elements[`setting-input-${key}`] = input;
    elements[`setting-status-${key}`] = {textContent: '', classList: {toggle() {}}};
    elements[`setting-retry-${key}`] = {hidden: true};
    return input;
}
const requests = [];
const context = vm.createContext({window: {}, localStorage: {setItem() {}},
    document: {getElementById: id => elements[id] || null},
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    API: {saveSettings(key, value) { return new Promise(resolve => requests.push({key, value, resolve})); }},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const ui = context.ui;
const notifications = [];
ui.showToast = text => notifications.push(text);
async function tick() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
async function flushTimers() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); await tick(); }
async function run() {
    const text = field('system_prompt', 'original');
    text.value = 'first'; ui.handleSettingChange('system_prompt');
    text.value = 'latest'; ui.handleSettingChange('system_prompt');
    assert.equal(requests.length, 0, 'Typing is debounced');
    assert.equal(timers.size, 1);
    await flushTimers();
    assert.equal(requests[0].value, 'latest');
    text.value = 'newer'; ui.handleSettingChange('system_prompt');
    requests[0].resolve({system_prompt: 'latest'}); await tick();
    assert.equal(text.value, 'newer');
    assert.ok(text.classList.contains('dirty'), 'Older response does not clear a newer edit');
    await flushTimers();
    requests[1].resolve({system_prompt: 'newer'}); await ui._settingSaveQueue;
    assert.equal(text.dataset.original, 'newer');
    assert.equal(notifications.at(-1), 'Kaydedildi');
    assert.equal(elements['setting-status-system_prompt'].textContent, '');
    assert.ok(!text.classList.contains('dirty'));
    const model = field('router_model_group', 'a', 'SELECT');
    model.value = 'b'; ui.handleSettingChange('router_model_group'); await tick();
    assert.equal(requests[2].value, 'b', 'Selections save immediately');
    assert.equal(elements['setting-status-router_model_group'].textContent, 'Kaydediliyor…', 'Pending save gives immediate feedback');
    const notificationsBeforeSave = notifications.length;
    model.value = 'a'; ui.handleSettingChange('router_model_group');
    requests[2].resolve({router_model_group: 'b'}); await tick();
    assert.equal(notifications.length, notificationsBeforeSave, 'Superseded save does not show a success notification');
    assert.equal(requests[3].value, 'a', 'Reverting during a request still saves the final choice');
    requests[3].resolve({error: 'offline'}); await ui._settingSaveQueue;
    assert.ok(model.classList.contains('dirty'));
    assert.equal(elements['setting-retry-router_model_group'].hidden, false);
    ui.saveSetting('router_model_group'); await tick();
    requests[4].resolve({router_model_group: 'a'}); await ui._settingSaveQueue;
    assert.equal(elements['setting-retry-router_model_group'].hidden, true);
    const temp = field('temperature', '0.9');
    temp.value = ''; ui.handleSettingChange('temperature'); await flushTimers();
    assert.equal(requests[5].value, '', 'Clearing temperature saves the model default');
    requests[5].resolve({temperature: null}); await ui._settingSaveQueue;
    temp.value = '9'; temp.validity.valid = false; temp.validationMessage = 'Invalid';
    ui.handleSettingChange('temperature'); await flushTimers();
    assert.equal(requests.length, 6, 'Invalid values are not sent');
    console.log('Autosave debounce, ordering, newer edits, errors, retry and blank defaults passed');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
