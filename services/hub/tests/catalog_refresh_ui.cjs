const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const button = { disabled: false, title: 'Model ve ses listesini yenile' };
let resolveCatalog;
const selects = {};
const context = vm.createContext({ window: {}, Option: function(text, value) { this.text = text; this.value = value; },
    API: { getChatModels: () => new Promise(resolve => { resolveCatalog = resolve; }) },
    document: {
        activeElement: null,
        getElementById: id => id === 'refresh-models' ? button : selects[id] || null,
        createElement: () => ({}),
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);

async function run() {
    const silent = context.ui.loadModelChoices({}, true);
    assert.equal(button.disabled, false, 'Automatic refresh must not animate/disable the button');
    assert.equal(button.title, 'Model ve ses listesini yenile');
    resolveCatalog({ models: [], voices: {}, unavailable: [] });
    await silent;
    const selected = {text: 'old', value: 'old'};
    const select = {
        options: [selected], value: 'old', dataset: {original: 'saved', saving: 'true'},
        insertBefore(option, before) {
            const existing = this.options.indexOf(option);
            if (existing >= 0) this.options.splice(existing, 1);
            const index = before ? this.options.indexOf(before) : this.options.length;
            this.options.splice(index, 0, option);
        },
        remove(index) { this.options.splice(index, 1); },
    };
    selects['setting-input-router_model_group'] = select;
    context.document.activeElement = select;
    let finishVoices;
    context.API.getVoiceCatalog = () => new Promise(resolve => { finishVoices = resolve; });
    const refresh = context.ui.loadModelChoices({}, true);
    resolveCatalog({models: [{name: 'old', capability: 'chat'}, {name: 'new', capability: 'chat'}], voices: {}, unavailable: []});
    await refresh;
    assert.deepEqual(select.options.map(option => option.value), ['old', 'new'], 'Focused picker receives new models');
    assert.equal(select.options[0], selected, 'Existing options retain their nodes');
    assert.equal(select.value, 'old', 'Pending selection is preserved');
    assert.equal(select.dataset.original, 'saved', 'Refresh does not acknowledge unsaved edits');
    assert.equal(context.ui._voiceCatalogLoading, true, 'Models render before slow voices finish');
    finishVoices({voices: {}, unavailable: []});
    assert.equal(button.title, 'Model ve ses listesini yenile');

    const manual = context.ui.loadModelChoices({}, false);
    assert.equal(button.disabled, true, 'Manual refresh shows its loading state');
    assert.equal(button.title, 'Güncelleniyor…');
    resolveCatalog({ models: [], voices: {}, unavailable: [] });
    await manual;
    assert.equal(button.disabled, false);
    assert.equal(button.title, 'Model ve ses listesini yenile');
    console.log('Automatic catalog refresh leaves the manual button unchanged');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
