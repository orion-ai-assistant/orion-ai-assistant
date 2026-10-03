const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/mobile.js'), 'utf8');

function setup() {
    const handlers = {}, classes = new Set(), properties = new Map();
    let focused;
    const element = id => ({id, inert: false, attributes: {}, handlers: {},
        setAttribute(key, value) { this.attributes[key] = value; },
        addEventListener(key, fn) { this.handlers[key] = fn; },
        focus() { focused = id; }, querySelectorAll() { return []; }});
    const sidebar = element('app-sidebar'), main = element('main');
    const elements = Object.fromEntries(['mobile-menu-toggle', 'mobile-sidebar-close', 'sidebar-backdrop'].map(id => [id, element(id)]));
    elements['app-sidebar'] = sidebar;
    const media = {matches: true, addEventListener(key, fn) { this.change = fn; }};
    const viewport = {height: 700, offsetTop: 0, scale: 1, handlers: {}, addEventListener(key, fn) { this.handlers[key] = fn; }};
    const document = {body: {classList: {toggle(key, value) { value ? classes.add(key) : classes.delete(key); }, contains(key) { return classes.has(key); }}},
        documentElement: {style: {setProperty(key, value) { properties.set(key, value); }, removeProperty(key) { properties.delete(key); }}},
        getElementById: id => elements[id], querySelector: () => main,
        addEventListener(key, fn) { handlers[key] = fn; }};
    vm.runInNewContext(source, {document, window: {matchMedia: () => media, visualViewport: viewport, innerHeight: 700, addEventListener() {}}});
    handlers.DOMContentLoaded();
    return {elements, sidebar, main, media, viewport, properties, handlers, focused: () => focused};
}

test('mobile menu keeps hidden controls unfocusable, opens and closes with Escape', () => {
    const ui = setup();
    assert.equal(ui.sidebar.inert, true);
    ui.elements['mobile-menu-toggle'].handlers.click();
    assert.equal(ui.sidebar.inert, false);
    assert.equal(ui.main.inert, true);
    assert.equal(ui.focused(), 'mobile-sidebar-close');
    ui.handlers.keydown({key: 'Escape', preventDefault() {}});
    assert.equal(ui.main.inert, false);
    assert.equal(ui.sidebar.inert, true);
    assert.equal(ui.focused(), 'mobile-menu-toggle');
});

test('keyboard viewport shrinks composer area; desktop restores navigation', () => {
    const ui = setup();
    ui.viewport.height = 350; ui.viewport.offsetTop = 12;
    ui.viewport.handlers.resize();
    assert.equal(ui.properties.get('--mobile-height'), '350px');
    assert.equal(ui.properties.get('--mobile-top'), '12px');
    ui.elements['mobile-menu-toggle'].handlers.click();
    ui.media.matches = false; ui.media.change();
    assert.equal(ui.sidebar.inert, false);
    assert.equal(ui.main.inert, false);
    assert.equal(ui.properties.has('--mobile-height'), false);
});

test('selecting a chat or settings closes drawer', () => {
    const ui = setup();
    ui.elements['mobile-menu-toggle'].handlers.click();
    ui.sidebar.handlers.click({target: {closest: () => ({})}});
    assert.equal(ui.sidebar.inert, true);
    assert.equal(ui.main.inert, false);
});
