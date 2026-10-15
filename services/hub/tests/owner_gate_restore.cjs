const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../src/orion/api/owner_ui/gate.js'),'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function boot(saved, defaultPassword = 'orion') {
 let startup, request, resolveRequest, rejectRequest, cleared = false, unlocked = false, focus = 0;
 const nodes = {input:{value:'',focus(){focus++;}},button:{},'[data-default-password]':{hidden:true,textContent:''},'[role="alert"]':{},form:{addEventListener(){}}};
 const gate = {hidden:false,querySelector:key=>nodes[key],remove(){this.removed=true;}};
 const context = {window:{},document:{
  body:{dataset:{ownerScope:'admin'},appendChild(node){assert.equal(node.hidden,true,'Gate must already be hidden before insertion');},classList:{remove(){unlocked=true;}}},
  createElement:()=>gate,addEventListener(name,fn){if(name==='DOMContentLoaded')startup=fn;},dispatchEvent(){},getElementById(){return null;}
 },OwnerAPI:{url:x=>x,base:'/panel',sessionKey:'session',session:()=>cleared?null:saved,clearSession(){cleared=true;},
  fetch(url){if(url === '/api/v1/owner/secure/password-info') return defaultPassword === 'offline' ? Promise.reject(new Error('offline')) : Promise.resolve({ok:true,json:async()=>({default_password:defaultPassword})});request=url;return new Promise((resolve,reject)=>{resolveRequest=resolve;rejectRequest=reject;});}},
 crypto:{randomUUID:()=> 'tab'},Event:class{},setTimeout(fn,ms){if(ms===150)Promise.resolve().then(fn);},Date,
 sessionStorage:{},location:{reload(){}}};
 vm.runInNewContext(source,context);context.OwnerGate=context.window.OwnerGate;startup();
 return {gate,hint:nodes['[data-default-password]'],get focus(){return focus;},get unlocked(){return unlocked;},get request(){return request;},
  respond(scope){resolveRequest({ok:scope!==null,json:async()=>({scope})});},fail(){rejectRequest(new Error('offline'));}};
}
(async()=>{
 const saved={token:'existing',expires:Date.now()+900000};
 const valid=boot(saved);await settle();assert.equal(valid.request,'/api/v1/owner/session');
 assert.equal(valid.gate.hidden,true,'Password prompt remains hidden during the delayed session check');assert.equal(valid.focus,0);
 valid.respond('admin');await settle();assert(valid.unlocked);assert(valid.gate.removed);assert(valid.gate.hidden);assert.equal(valid.focus,0);
 const invalid=boot(saved);await settle();invalid.respond(null);await settle();assert(!invalid.unlocked);assert(!invalid.gate.hidden);assert.equal(invalid.focus,1);
 const absent=boot(null);await settle();assert(!absent.gate.hidden);assert.equal(absent.request,undefined);assert.equal(absent.focus,1);
 assert(!absent.gate.innerHTML.includes('Başlangıca dön'));assert.equal(absent.hint.textContent,'Varsayılan yönetici şifresi: orion');assert.equal(absent.hint.hidden,false);
 const changed=boot(null,null);await settle();assert.equal(changed.hint.hidden,true);assert.equal(changed.hint.textContent,'');
 const failedHint=boot(null,'offline');await settle();assert.equal(failedHint.hint.hidden,true);assert.equal(failedHint.hint.textContent,'');
 const offline=boot(saved);await settle();offline.fail();await settle();assert(!offline.unlocked);assert(!offline.gate.hidden);
 console.log('Valid tab session never flashes password UI; absent, rejected and failed sessions show login');
})().catch(error=>{console.error(error);process.exitCode=1;});
