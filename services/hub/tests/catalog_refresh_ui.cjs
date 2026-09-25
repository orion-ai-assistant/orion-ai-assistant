const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const button = { disabled: false, title: 'Model ve ses listesini yenile' };
let resolveCatalog;
const context = vm.createContext({ window: {}, Option: function(text, value) { this.text = text; this.value = value; },
    API: { getChatModels: () => new Promise(resolve => { resolveCatalog = resolve; }) },
    document: {
        activeElement: null,
        getElementById: id => id === 'refresh-models' ? button : null,
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
