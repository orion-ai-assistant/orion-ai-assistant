const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../src/orion/api');
const html = name => fs.readFileSync(path.join(root, 'owner_ui', name), 'utf8');
assert(!html('local.html').includes('/panel/manage/security'), 'Landing page QR shortcut is not required by pairing clients');
assert(html('local.html').includes('href="/hub"'), 'Chat remains available at the local landing page');
for (const name of ['index.html', 'people.html']) assert(!html(name).includes('/dashboard/'));
assert(!fs.readFileSync(path.join(root, 'admin_ui/index.html'), 'utf8').includes('/dashboard/'));
assert(html('index.html').includes('class="owner-locked" data-owner-scope="admin"'));
assert(html('index.html').includes('gate.js?v=6'));
assert(!html('security.html').includes('owner-password-form'));
assert(html('people.html').includes('data-owner-scope="admin"'), 'People uses the same admin session as the panel');
let locked = 0, calls = [], response = {ok: true, json: async () => ({ok: true})};
const submit = {disabled: false}, error = {hidden: true};
const current = {value: 'old', focus() {}}, next = {value: 'new-password'};
const form = {elements: {current, next}, querySelector: () => submit, reset() {current.value = next.value = '';}};
const dialog = {open: false, addEventListener(name, fn) {this[name] = fn;}, showModal() {this.open = true;}, close() {this.open = false; this.closeEvent?.();}};
dialog.addEventListener = (name, fn) => { if (name === 'close') dialog.closeEvent = fn; };
const nodes = {'owner-password-dialog':dialog, 'owner-password-form':form, 'owner-password-error':error, 'owner-password-open':{}, 'owner-password-close':{}};
const gate = {unlocked: false, lock() {locked++;}};
vm.runInNewContext(fs.readFileSync(path.join(root, 'owner_ui/panel.js'), 'utf8'), {
 document: {getElementById: id => nodes[id]}, OwnerGate: gate,
 OwnerAPI: {fetch: async (url, options) => {calls.push({url, options}); return response;}}
});
(async () => {
 nodes['owner-password-open'].onclick(); assert(!dialog.open, 'Locked content cannot open password settings');
 gate.unlocked = true; nodes['owner-password-open'].onclick(); assert(dialog.open);
 current.value = 'old'; next.value = 'new-password';
 await form.onsubmit({preventDefault(){}});
 assert.equal(calls[0].url, '/api/v1/owner/secure/password');
 assert.deepEqual(JSON.parse(calls[0].options.body), {current_password:'old', new_password:'new-password'});
 assert.equal(current.value, ''); assert.equal(next.value, '');
 assert.equal(locked, 1, 'Successful password change locks the current tab immediately');
 assert(!dialog.open); assert(!submit.disabled);
 response = {ok:false, json:async()=>({detail:'Mevcut şifre yanlış.'})};
 nodes['owner-password-open'].onclick(); current.value='wrong';next.value='new-password';
 await form.onsubmit({preventDefault(){}});
 assert.equal(error.textContent,'Mevcut şifre yanlış.');assert.equal(error.hidden,false);
 assert(dialog.open);assert.equal(locked,1);assert.equal(current.value,'');assert.equal(next.value,'');
 nodes['owner-password-close'].onclick();assert(!dialog.open);assert(error.hidden);
 console.log('Panel entry requires admin auth; password settings moved, cleared and lock after success');
})().catch(error => {console.error(error);process.exitCode=1;});
