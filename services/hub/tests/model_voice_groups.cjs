const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const select = {value: '', options: [], replaceChildren(option) {this.options = [option]; this.value = option.value;}, add(option) {this.options.push(option);}, parentElement: {previousElementSibling: null, appendChild() {}}};
const model = {value: 'family-exact'};
const context = vm.createContext({window: {}, OrionI18n: {t: k => k},
    Option: function(text,value) {this.text = text; this.value = value;},
    document: {getElementById: id => ({'setting-input-tts_voice': select, 'setting-input-tts_model': model})[id] || null,
        createElement: () => ({style: {}})}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const ui = context.ui;
ui.routerCatalog = {models: [{name:'family-exact',provider:'provider'}, {name:'family-other',provider:'provider'}],
    voices: {provider:['A','B','C']}, model_voice_groups: {provider:[
        {match_patterns:['family'],voices:['A']}, {models:['family-exact'], voices:['B']} ]}, unavailable:[]};
ui.getRememberedVoice = () => 'C';
let saves = 0; ui.handleSettingChange = () => {saves++;};
ui.loadVoiceChoices();
assert.deepEqual(select.options.map(o => o.value), ['', 'B']);
assert.equal(select.value, ''); assert.equal(saves, 1);
model.value = 'family-other'; ui.getRememberedVoice = () => 'A'; ui.loadVoiceChoices();
assert.deepEqual(select.options.map(o => o.value), ['', 'A']); assert.equal(select.value, 'A');
ui.routerCatalog.unavailable = ['voices']; ui.getRememberedVoice = () => 'C'; ui.loadVoiceChoices();
assert.equal(select.value, 'C'); assert.equal(saves, 1, 'Unavailable catalog must not clear remembered preference');
console.log('Model voice groups survive exact/pattern matching, stale preference and outage');
