const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const buttons = new Map(['pair-device-sidebar', 'pair-device-settings'].map(id => [id, {hidden:true}]));
let streams = 0, closed = 0, requests = 0;
class FakeSource {
    constructor(url) { assert.equal(url, '/api/v1/pair/requests/stream?token=account'); streams++; }
    close() { closed++; }
    addEventListener(name, handler) { if (name === 'pairing_request') this.deliver = handler; else { assert.equal(name, 'pairing_completed'); this.completed = handler; } }
}
const context = vm.createContext({location:{hostname:'localhost'}, HubPicker:{connected:true,self:{id:'pc',local_entry:true},selected:{id:'pc'}},
    Auth:{getToken:()=> 'account'}, EventSource:FakeSource, document:{getElementById:id=>buttons.get(id)},
    clearInterval(){}, fetch:async(url, options)=>{requests++;assert.equal(url,'/panel/api/v1/owner/login');assert.equal(options.credentials,'same-origin');return {ok:true,json:async()=>({scope:'admin'})};}
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/orion/api/ui/js/device_pairing.js'),'utf8')+'\nglobalThis.pair = DevicePairing;',context);
(async()=>{
    context.pair.start(); assert.equal(streams,1);assert.equal(buttons.get('pair-device-sidebar').hidden,true);assert.equal(buttons.get('pair-device-settings').hidden,false);
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
    context.HubPicker.selected={id:'other'};context.pair.start();assert.equal(streams,1);assert.equal(closed,1);assert.equal(buttons.get('pair-device-settings').hidden,true);
    await assert.rejects(context.pair.owner('/login',{}));assert.equal(requests,1,'Remote peer never receives local owner credentials');
    context.HubPicker.selected={id:'pc'};context.location.hostname='192.168.1.108';context.pair.start();assert.equal(streams,1,'LAN page cannot enable local owner UI');
    console.log('Pairing UI local scope, push lifecycle, duplicate prompts and explicit owner approval passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
