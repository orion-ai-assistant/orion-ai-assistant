const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = {};
function Option(text, value) { this.text = text; this.value = value; }
const voice = elements['setting-input-tts_voice'] = {
    value: 'zephyr2', options: [new Option('zephyr2', 'zephyr2')],
    parentElement: { appendChild: node => { elements[node.id] = node; } },
    replaceChildren(...nodes) { this.options = nodes; }, add(node) { this.options.push(node); },
};
elements['setting-input-tts_model'] = { value: 'local-tts' };
const context = vm.createContext({ window: {}, Option, document: {
    getElementById: id => elements[id], createElement: () => ({}),
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const ui = context.ui;
ui.currentSettings = { tts_enabled: true, tts_voice: 'zephyr2' };
ui.routerCatalog = { models: [{ name: 'local-tts', provider: 'local' }], voices: {} };
ui.handleSettingChange = () => {};
ui.loadVoiceChoices(false);
assert.equal(voice.value, 'zephyr2');
assert.match(voice.options[1].text, /erişilemiyor/);
assert.notEqual(voice.options[1].disabled, true);
ui.loadVoiceChoices(true);
assert.equal(voice.value, 'zephyr2', 'Missing catalog must not erase a saved voice');
ui.routerCatalog.voices.local = ['zephyr2', 'new-voice'];
ui.loadVoiceChoices(false);
assert.equal(voice.options[1].text, 'zephyr2');
assert.equal(voice.options[2].value, 'new-voice');
assert.equal(elements['tts-availability'].textContent, '');
ui.currentSettings.tts_enabled = false;
ui.loadVoiceChoices(false);
assert.match(elements['tts-availability'].textContent, /kapalı/);
console.log('Voice availability and recovery tests passed');
