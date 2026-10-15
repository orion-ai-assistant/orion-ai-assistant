const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('image ingress preserves transparent PNG data and deduplicates content', () => {
    const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/main.js'), 'utf8');
    const start = source.indexOf('    let attachmentSequence = 0;');
    const end = source.indexOf('    if (imageUploadBtn && imageUploadInput)', start);
    const UI = { selectedImages: [], showToast: () => {} };
    const data = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB';
    const context = vm.createContext({
        UI, renderImagePreviews() {},
        FileReader: class {
            readAsDataURL() { this.result = data; this.onload(); }
        },
        Image: class { set src(value) { this.onload(); } },
        document: { createElement() { throw Error('Original images must not be flattened onto canvas'); } },
    });
    vm.runInContext(source.slice(start, end) + '\nglobalThis.processFiles = processFiles;', context);
    context.processFiles([{ name: 'transparent.png', size: 20, type: 'image/png' }]);
    assert.equal(UI.selectedImages.length, 1);
    assert.equal(UI.selectedImages[0].data, data);
    assert.equal(UI.selectedImages[0].mime_type, 'image/png');
    assert.equal(UI.selectedImages[0].loading, false);
    context.processFiles([{ name: 'renamed.png', size: 20, type: 'image/png' }]);
    assert.equal(UI.selectedImages.length, 1);
});
