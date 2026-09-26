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
ui.currentSettings = { tts_enabled: true, tts_model: 'local-tts', tts_voice: 'zephyr2' };
ui.routerCatalog = { models: [{ name: 'local-tts', provider: 'local' }], voices: {} };
ui.handleSettingChange = () => {};
ui.loadVoiceChoices(false);
assert.equal(voice.value, 'zephyr2');
assert.match(voice.options[1].text, /erişilemiyor/);
assert.notEqual(voice.options[1].disabled, true);
assert.equal(elements['tts-availability'].textContent, 'Orion TTS’ye erişilemiyor.');
elements['setting-input-tts_model'].value = 'gemini-tts';
ui.routerCatalog.models.push({ name: 'gemini-tts', provider: 'google' });
ui.routerCatalog.unavailable = ['local-tts-info'];
ui.loadVoiceChoices(false);
ui.routerCatalog.voices.google = ['gemini-voice-1', 'gemini-voice-2'];
ui.loadVoiceChoices(false);
assert.equal(elements['tts-availability'].textContent, '', 'Local TTS outage is hidden for gemini model with voices');
// Non-local models do not show Orion TTS outage notice
elements['setting-input-tts_model'].value = 'gemini-tts-no-voices';
ui.routerCatalog.models.push({ name: 'gemini-tts-no-voices', provider: 'google-beta' });
ui.loadVoiceChoices(false);
assert.equal(elements['tts-availability'].textContent, '', 'Non-local model without voices does not show Orion TTS notice');
elements['setting-input-tts_model'].value = 'local-tts';
ui.loadVoiceChoices(true);
assert.equal(voice.value, 'zephyr2', 'Missing catalog must not erase a saved voice');
ui.routerCatalog.voices.local = ['zephyr2', 'new-voice'];
ui.routerCatalog.unavailable = [];
ui.loadVoiceChoices(false);
assert.equal(voice.options[1].text, 'zephyr2');
assert.equal(voice.options[2].value, 'new-voice');
assert.equal(elements['tts-availability'].textContent, '', 'Voices populated — no notice shown');
ui.currentSettings.tts_enabled = false;
ui.loadVoiceChoices(false);
assert.match(elements['tts-availability'].textContent, /kapalı/);
assert.ok(!elements['tts-availability'].textContent.includes('korunuyor'), 'No secondary korunuyor message');
console.log('Voice availability and recovery tests passed');
