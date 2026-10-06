const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const buttons = new Map(['pair-device-sidebar', 'pair-device-settings', 'pair-device-panel'].map(id => [id, {hidden:true}]));
let streams = 0, closed = 0, requests = 0;
class FakeSource {
    constructor(url) { assert.equal(url, '/api/v1/pair/requests/stream?token=account'); streams++; }
    close() { closed++; }
    addEventListener(name, handler) { if (name === 'pairing_request') this.deliver = handler; else { assert.equal(name, 'pairing_completed'); this.completed = handler; } }
}
const context = vm.createContext({location:{hostname:'localhost'}, HubPicker:{connected:true,self:{id:'pc',local_entry:true},selected:{id:'pc'}},
    Auth:{getToken:()=> 'account'}, EventSource:FakeSource, document:{getElementById:id=>buttons.get(id), addEventListener(){}},
    clearInterval(){}, setInterval(){return 1;}, fetch:async(url, options)=>{requests++;assert.equal(url,'/panel/api/v1/owner/login');assert.equal(options.credentials,'same-origin');return {ok:true,json:async()=>({scope:'admin'})};}
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/orion/api/ui/js/device_pairing.js'),'utf8')+'\nglobalThis.pair = DevicePairing;',context);
(async()=>{
    context.HubPicker.connected=false;context.pair.renderControls();
    assert.equal(buttons.get('pair-device-panel').hidden,false,'Panel is visible before account/SSE startup');
    assert.equal(buttons.get('pair-device-settings').disabled,true);
    context.HubPicker.connected=true;context.pair.renderControls();
    assert.equal(buttons.get('pair-device-settings').disabled,false,'Late connection enables QR without another login');
    context.pair.start(); assert.equal(buttons.get('pair-device-panel').hidden,false); assert.equal(streams,1);assert.equal(buttons.get('pair-device-sidebar').hidden,true);assert.equal(buttons.get('pair-device-settings').hidden,false);
    let prompts = 0; context.pair.open=()=>prompts++;
    const event = {data:JSON.stringify({request_id:'once',hub_id:'pc',device_name:'Phone',expires_at:Math.floor(Date.now()/1000)+60})};
    context.pair.source.deliver(event);context.pair.source.deliver(event);assert.equal(prompts,1);assert.equal(requests,0,'A request only prompts: no automatic owner login or pairing');
    let qrClosed = 0; context.pair.dialog = {open:true,close(){qrClosed++;this.open=false;}}; context.pair.pairingId='qr-ours';
    context.pair.source.completed({data:JSON.stringify({hub_id:'other-pc',pairing_id:'other'})}); assert.equal(qrClosed,0);
    context.pair.source.completed({data:JSON.stringify({hub_id:'pc',pairing_id:'other-qr'})}); assert.equal(qrClosed,1); context.pair.dialog.open=true;
    context.pair.passwordMode=true;
    context.pair.source.completed({data:JSON.stringify({hub_id:'pc',pairing_id:'another-qr'})});assert.equal(qrClosed,1,'Password-change form stays open');
    context.pair.passwordMode=false;context.pair.request={request_id:'phone-request'};
    context.pair.source.completed({data:JSON.stringify({hub_id:'pc',pairing_id:'other-qr',request_id:'other-request'})});assert.equal(qrClosed,2,'All QR panels for this Hub close after successful pairing');context.pair.dialog=null;
    await context.pair.owner('/login',{password:'test',scope:'admin'});assert.equal(requests,1);
    context.HubPicker.selected={id:'other'};context.pair.start();assert.equal(streams,1);assert.equal(closed,1);assert.equal(buttons.get('pair-device-settings').disabled,true);assert.equal(buttons.get('pair-device-panel').hidden,false,'Local panel stays visible when unavailable');
    await assert.rejects(context.pair.owner('/login',{}));assert.equal(requests,1,'Remote peer never receives local owner credentials');
    context.HubPicker.selected={id:'pc'};context.location.hostname='192.168.1.108';context.pair.start();assert.equal(streams,1,'LAN page cannot enable local owner UI');
    const fields = new Map(['[data-owner]', '[data-qr]', '[data-result]', '[data-password]', '[data-password-change]', '[data-generate]', '[data-default]', '[data-expiry]', '[data-copy]'].map(selector => [selector, {hidden:true, removeAttribute(){}, insertBefore(){}}]));
    context.pair.dialog = {open:true, querySelector: selector => fields.get(selector)};
    context.pair.owner = async () => ({qr_png:'test-png', link:'https://orion.local/connect', pairing_id:'new-qr', expires_in:120});
    await context.pair.generate(context.pair.epoch);
    assert.equal(fields.get('[data-result]').hidden,false);
    assert.equal(fields.get('[data-qr]').src,'data:image/png;base64,test-png');
    assert.equal(fields.get('[data-generate]').hidden,true,'Renewal stays hidden while QR is valid');
    context.pair.remoteMode=true;let renewals=0;context.pair.createRemote=async password=>{assert.equal(password,'owner-secret');renewals++;return {qr_png:'test',link:'https://hub/connect',pairing_id:'expired',expires_in:0};};await context.pair.generate(context.pair.epoch,'owner-secret');assert.equal(context.pair.remotePassword,'owner-secret');assert.equal(fields.get('[data-generate]').hidden,false,'Expired QR offers renewal');assert.equal(fields.get('[data-owner]').hidden,true,'Expiry never reveals the password form');await context.pair.generate(context.pair.epoch,context.pair.remotePassword);assert.equal(renewals,2);
    console.log('Pairing UI local scope, push lifecycle, duplicate prompts and explicit owner approval passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
