const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const timers = new Map();
let nextId = 0;
const body = {children: [], appendChild(node) { this.children.push(node); }};
const context = vm.createContext({window: {}, document: {body, getElementById() { return null; },
    createElement() { return {attributes: {}, setAttribute(k, v) { this.attributes[k] = v; }, showPopover() {this.open = true;}, hidePopover() {this.open = false;}}; }},
    setTimeout(fn, delay) { assert.equal(delay, 1600); timers.set(++nextId, fn); return nextId; },
    clearTimeout(id) { timers.delete(id); },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
context.ui.showToast('Kaydedildi');
const toast = body.children[0];
assert.equal(toast.open, true, 'Toast enters the top layer, including above tool dialogs');
assert.equal(toast.attributes.role, 'status');
context.ui.showToast('Kaydedildi');
assert.equal(body.children.length, 1, 'Repeated saves reuse one notification');
assert.equal(timers.size, 1, 'Repeated saves refresh the expiry');
[...timers.values()][0]();
assert.equal(toast.hidden, true);
assert.equal(toast.open, false);
console.log('Transient save notification lifetime and reuse passed');
