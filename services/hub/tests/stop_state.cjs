const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({ performance, console, window: {}, document: {
    getElementById: () => ({ disabled: false, innerHTML: '' })
}});
for (const file of ['config.js', 'api.js', 'sse.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js', file), 'utf8'), context);
}
vm.runInContext(`
    const received = [];
    let finished = false;
    const UI = {
        appendToken: (chat, token) => received.push(token),
        finishGeneration: () => { finished = true; },
        setStopButtonVisible: () => {},
    };
    AppState.currentChatId = 'chat';
    AppState.startGenerating('chat', 'turn');
    API._fetch = async () => ({ok: true});
    globalThis.result = (async () => {
        await API.stopGeneration();
        const waiting = AppState.isStopping('chat') && !finished;
        SSE.processEvent({type:'token', chat_id:'chat', turn_id:'old', data:{token:'wrong'}});
        SSE.processEvent({type:'token', chat_id:'chat', turn_id:'turn', data:{token:'tail'}});
        SSE.processEvent({type:'done', chat_id:'chat', turn_id:'turn', data:{status:'stopped'}});
        return {waiting, text:received.join(''), finished, active:AppState.isGenerating()};
    })();
`, context);
context.result.then(result => {
    assert.equal(result.waiting, true);
    assert.equal(result.text, 'tail');
    assert.equal(result.finished, true);
    assert.equal(result.active, false);
    console.log('Stop drains matching turn until done: passed');
}).catch(error => { console.error(error); process.exitCode = 1; });
