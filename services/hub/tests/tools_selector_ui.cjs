const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
        this.className = ''; this._text = ''; this.hidden = false;
        this.classList = {
            add: name => { if (!this.className.split(' ').includes(name)) this.className = `${this.className} ${name}`.trim(); },
            remove: name => { this.className = this.className.split(' ').filter(item => item !== name).join(' '); },
            toggle: (name, force) => { if (force) this.classList.add(name); else this.classList.remove(name); },
            contains: name => this.className.split(' ').includes(name),
        };
    }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._text = String(value); this.children = []; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener() {}
    querySelectorAll(selector) {
        const className = selector.slice(1);
        return this.children.flatMap(child => [
            ...(selector.startsWith('.') && child.className.split(' ').includes(className) ? [child] : []),
            ...child.querySelectorAll(selector),
        ]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    showModal() { this.open = true; }
    close() { this.open = false; }
}

const nodes = Object.fromEntries(['tools-list', 'tools-error', 'tools-save', 'tools-reset', 'tools-title', 'tools-source', 'tools-close']
    .map(id => [id, new Element()]));
const dialog = new Element('dialog');
dialog.querySelector = selector => selector === '.tools-close' ? nodes['tools-close'] :
    nodes[selector.slice(1)] || Element.prototype.querySelector.call(dialog, selector);
const context = vm.createContext({
    window: {}, structuredClone, console,
    AppConfig: {getUserId: () => 'u'}, AppState: {currentChatId: null},
    document: {createElement: tag => new Element(tag), addEventListener() {}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/tools.js'), 'utf8') + '\nglobalThis.tools = ToolsUI;', context);
const tools = context.tools;
tools.dialog = dialog;
tools.request = async url => url === '/api/v1/tools'
    ? [{id: 'datetime', name: 'Tarih ve saat', description: 'Güncel tarih ve saat bilgileri.',
        functions: [{id: 'get_current_time', name: 'Güncel saat', description: 'Current time.'}]},
       {id: 'text', name: 'Metin işlemleri', description: 'Metni işle.',
        functions: [
            {id: 'analyze_text', name: 'Metni analiz et', description: 'Count.'},
            {id: 'find_in_text', name: 'Metinde bul', description: 'Find.'},
            {id: 'replace_in_text', name: 'Metni değiştir', description: 'Replace.'},
        ]}]
    : {tool_selection: {categories: {datetime: true}, functions: {get_current_time: true}}};

(async () => {
    await tools.open(false);
    const list = nodes['tools-list'];
    const categories = list.querySelectorAll('.tool-category');
    assert.equal(categories.length, 2);
    assert.equal(categories[0].classList.contains('is-expanded'), false, 'Every category starts collapsed');
    assert.equal(categories[1].classList.contains('is-expanded'), false, 'Other categories stay collapsed');
    assert.equal(categories[0].querySelector('.tool-category-reveal').inert, true);
    assert.equal(categories[1].querySelector('.tool-category-reveal').inert, true);
    assert.equal(categories[0].querySelectorAll('.tool-row').length, 1);
    assert.equal(categories[1].querySelectorAll('.tool-row').length, 3);
    const row = categories[0].querySelector('.tool-category-row');
    const button = row.querySelector('.tool-category-button');
    assert.equal(button.attributes['aria-expanded'], 'false');
    button.onclick();
    assert.equal(categories[0].classList.contains('is-expanded'), true);
    assert.equal(button.attributes['aria-expanded'], 'true');
    const textButton = categories[1].querySelector('.tool-category-button');
    textButton.onclick();
    assert.equal(categories[1].classList.contains('is-expanded'), true);
    assert.equal(categories[1].querySelector('.tool-category-reveal').inert, false);
    assert.equal(button.attributes['aria-expanded'], 'true', 'Categories expand independently');
    const categorySwitch = row.querySelector('.tool-switch');
    categorySwitch.checked = false; categorySwitch.onchange();
    assert.equal(categories[0].querySelector('.tools-functions').disabled, true);
    assert.equal(categories[0].querySelector('.tools-functions').querySelector('.tool-switch').checked, true, 'Child choice is retained');
    assert.equal(row.querySelector('.tool-category-status').textContent, '');
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(tools.draft.categories.datetime, false);
    assert.equal(tools.draft.functions.get_current_time, true);
    await nodes['tools-reset'].onclick();
    assert.equal(tools.draft, null, 'Reset removes the draft override');
    assert.equal(dialog.open, true, 'Reset keeps the dialog open');
    assert.equal(categorySwitch.checked, true, 'Reset restores current defaults');
    assert.equal(row.querySelector('.tool-category-status').textContent, '1/1');
    nodes['tools-save'].onclick();
    assert.equal(dialog.open, false, 'Done closes the dialog');
    context.AppState.currentChatId = 'chat-1';
    let rejectWrite;
    const writes = [];
    const catalog = await tools.request('/api/v1/tools');
    tools.request = async (url, method = 'GET', body) => {
        if (url === '/api/v1/tools') return catalog;
        if (url === '/api/v1/admin/settings') return {tool_selection: {categories: {datetime: true}, functions: {get_current_time: true}}};
        if (method === 'GET') return {selection: {categories: {datetime: true}, functions: {get_current_time: true}}, inherited: true};
        writes.push({method, body});
        if (rejectWrite) { const reject = rejectWrite; rejectWrite = null; reject(); throw new Error('Network failure'); }
        return {};
    };
    await tools.open(false);
    const chatCategory = nodes['tools-list'].querySelector('.tool-category');
    const chatSwitch = chatCategory.querySelector('.tool-category-row').querySelector('.tool-switch');
    chatSwitch.checked = false; chatSwitch.onchange();
    await new Promise(setImmediate);
    assert.equal(writes[0].method, 'PUT', 'Switch change saves immediately');
    assert.equal(writes[0].body.categories.datetime, false);
    assert.equal(tools.saving, false);
    rejectWrite = () => {};
    chatSwitch.checked = true; chatSwitch.onchange();
    await new Promise(setImmediate);
    assert.equal(chatSwitch.checked, false, 'Failed save restores last stored selection');
    assert.match(nodes['tools-error'].textContent, /Kaydedilemedi/);
    await nodes['tools-reset'].onclick();
    assert.equal(writes.at(-1).method, 'DELETE', 'Reset clears chat override');
    assert.equal(dialog.open, true);
    assert.equal(chatSwitch.checked, true);
    console.log('Collapsed categories autosave and reset without closing');
})().catch(error => { console.error(error); process.exitCode = 1; });
