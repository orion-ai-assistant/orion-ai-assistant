const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const panel = {hidden: true};
const button = {setAttribute(key, value) { this[key] = value; }};
let rendered = false;
const input = {value: '0.6', dataset: {original: '0.9'}, classList: {contains: () => true}};
const dashboard = {innerHTML: '', querySelector: () => rendered ? input : null};
const context = vm.createContext({window: {}, document: {
    activeElement: null,
    getElementById: id => ({'settings-dashboard': dashboard, 'setting-input-temperature': input,
        'settings-panel-advanced': panel, 'settings-advanced-toggle': button})[id] || null,
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
context.ui.renderSettings({router_model_group: 'test', chat_title_model: '', router_api_key: '', tts_model: 'tts', stt_model: 'stt', temperature: null, thinking_level: '', system_prompt: '<instructions>&test', token_delay_ms: 3000, tts_enabled: true});
const html = dashboard.innerHTML;
const general = html.split('id="settings-panel-general"')[1].split('</section>')[0];
const advanced = html.split('id="settings-panel-advanced"')[1].split('</section>')[0];
for (const key of ['router_model_group', 'chat_title_model', 'router_api_key', 'tts_model', 'stt_model']) {
    assert.ok(general.includes(`setting-input-${key}`), `${key} belongs to general settings`);
    assert.ok(!advanced.includes(`setting-input-${key}`));
}
assert.ok(!general.includes('setting-input-temperature'));
assert.ok(advanced.includes('setting-input-temperature'));
assert.ok(advanced.includes(' hidden>'));
assert.ok(advanced.includes('Demo akışı gecikmesi'));
assert.ok(advanced.includes('&lt;instructions&gt;&amp;test'));
assert.ok(!advanced.includes('value="null"'));
assert.ok(!html.includes('Varsayılana dön'));
assert.ok(!html.includes('Model/Router varsayılanını kullan'));
assert.ok(!html.includes('role="tab"'));
assert.ok(html.indexOf('id="settings-advanced-toggle"') > html.indexOf('</section>'));
context.ui.toggleAdvancedSettings();
assert.equal(panel.hidden, false);
assert.equal(button['aria-expanded'], 'true');
rendered = true;
context.ui.renderSettings({temperature: 0.9});
assert.equal(input.value, '0.6', 'Polling preserves unsaved edits');
assert.equal(panel.hidden, false, 'Polling keeps advanced settings expanded');
assert.equal(dashboard.innerHTML, html, 'Expanding and polling do not rebuild inputs');
context.ui.toggleAdvancedSettings();
assert.equal(panel.hidden, true);
assert.equal(button['aria-expanded'], 'false');
assert.equal(input.value, '0.6', 'Collapsing preserves unsaved edits');
console.log('General settings, advanced disclosure and draft preservation passed');
