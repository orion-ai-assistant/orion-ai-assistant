const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const button = { disabled: false };
const status = { textContent: 'Bu sayfa açıkken otomatik güncellenir' };
let resolveCatalog;
const context = vm.createContext({ window: {}, Option: function(text, value) { this.text = text; this.value = value; },
    API: { getChatModels: () => new Promise(resolve => { resolveCatalog = resolve; }) },
    document: {
        activeElement: null,
        getElementById: id => id === 'refresh-models' ? button : id === 'catalog-status' ? status : null,
        createElement: () => ({}),
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);

async function run() {
    const silent = context.ui.loadModelChoices({}, true);
    assert.equal(button.disabled, false, 'Automatic refresh must not animate/disable the button');
    assert.equal(status.textContent, 'Bu sayfa açıkken otomatik güncellenir');
    resolveCatalog({ models: [], voices: {}, unavailable: [] });
    await silent;
    assert.equal(status.textContent, 'Bu sayfa açıkken otomatik güncellenir');

    const manual = context.ui.loadModelChoices({}, false);
    assert.equal(button.disabled, true, 'Manual refresh shows its loading state');
    assert.equal(status.textContent, 'Güncelleniyor…');
    resolveCatalog({ models: [], voices: {}, unavailable: [] });
    await manual;
    assert.equal(button.disabled, false);
    assert.match(status.textContent, /Güncel/);
    console.log('Automatic catalog refresh leaves the manual button unchanged');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
