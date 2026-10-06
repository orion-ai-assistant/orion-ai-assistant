const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/main.js'), 'utf8');
const handlers = {}, selector = '.message.bot, .message.user';
let selected, prevented = 0;
const node = (text, excluded = false) => ({textContent: text, parentElement: {closest: () => excluded ? {} : null}});
const content = node('Answer'), tool = node('Function name'), timing = node('2.93 sn', true);
const assistant = {isConnected: true, nodes: [tool, content, timing]};
const userText = node('My question'), user = {isConnected: true, nodes: [userText]};
const selection = {removeAllRanges() {}, addRange: range => selected = range};
const context = {NodeFilter: {SHOW_TEXT: 4, FILTER_REJECT: 2, FILTER_ACCEPT: 1},
    document: {addEventListener: (type, handler) => handlers[type] = handler,
        createTreeWalker(message, _, filter) {const nodes = message.nodes.filter(n => filter.acceptNode(n) === 1); let index = 0; return {nextNode: () => nodes[index++]};},
        createRange: () => ({setStart(node) {this.start = node;}, setEnd(node) {this.end = node;}})},
    window: {getSelection: () => selection}};
vm.runInNewContext(source.slice(source.indexOf('// Drag selection')), context);
function select(message) {
    const target = {closest: value => value === selector ? message : null};
    handlers.pointerdown({target});
    handlers.keydown({key: 'a', ctrlKey: true, target, preventDefault() {prevented++;}});
}
select(assistant);
assert.equal(selected.start, tool);
assert.equal(selected.end, content, 'Audio timing stays outside the range');
select(user);
assert.equal(selected.start, userText);
assert.equal(selected.end, userText, 'User Select All stays in its own message');
handlers.keydown({key: 'a', ctrlKey: true, target: {closest: () => ({})}, preventDefault() {prevented++;}});
assert.equal(prevented, 2, 'Inputs retain native Select All');
handlers.pointerdown({target: {closest: () => null}});
handlers.keydown({key: 'a', ctrlKey: true, target: {closest: () => null}, preventDefault() {prevented++;}});
assert.equal(prevented, 2);
console.log('Assistant and user Select All are scoped; tool names included and audio timing excluded');
