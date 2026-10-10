const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const children = [], writes = [], notices = [];
const meta = {appendChild: child => children.push(child)};
const context = {window: {}, document: {getElementById() {}, createElement: () => ({setAttribute() {}})},
    navigator: {clipboard: {writeText: async value => writes.push(value)}}, OrionI18n: {t: k => k}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + ';globalThis.ui = UI;', context);
context.ui.showToast = text => notices.push(text);
context.ui.addMessageCopy({querySelector: selector => selector === '.message-meta' ? meta : null}, () => 'response');
(async () => {
    const button = children[0], original = button.innerHTML;
    await button.onclick();
    assert.deepEqual(writes, ['response']);
    assert.equal(button.innerHTML, original);
    assert.deepEqual(notices, ['messageCopied']);
    assert.match(original, /<svg/);
    console.log('Copy preserves its icon and shows clipboard feedback');
})().catch(e => {console.error(e); process.exitCode = 1;});
