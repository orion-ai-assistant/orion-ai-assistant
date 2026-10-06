const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ui=path.join(__dirname,'../src/orion/api/ui');
const main=fs.readFileSync(path.join(ui,'js/main.js'),'utf8');
async function authMode(local,invite=false){
 const nodes=new Map(); const node=()=>({hidden:false,value:'secret',textContent:'',style:{},removeAttribute(){},setAttribute(key,value){this[key]=value;},classList:{toggle(){}},disabled:false});
 const context={document:{getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);}},HubPicker:{apiPath:x=>x,self:{id:'self',registration_allowed:local},selected:{id:'self'}},Auth:{invitationToken:invite?'invite':null},fetch:async()=>({ok:true,json:async()=>({})})};
 vm.createContext(context);
 const start=main.indexOf("    const registerButton ="),end=main.indexOf('    document.getElementById("auth-login-btn").addEventListener',start);
 await vm.runInContext('(async()=>{const showAuthFailure=()=>{};'+main.slice(start,end)+'})()',context);
 return {nodes,context};
}
(async()=>{
 let {nodes}=await authMode(true);
 assert(nodes.get('auth-register-btn').hidden);assert(!nodes.get('auth-login-btn').hidden);
 nodes.get('auth-register-tab').onclick();assert(!nodes.get('auth-register-btn').hidden);assert(nodes.get('auth-login-btn').hidden);assert(!nodes.get('auth-confirm-group').hidden);assert.equal(nodes.get('auth-password').value,'');
 nodes.get('auth-login-tab').onclick();assert(nodes.get('auth-confirm-group').hidden);assert(nodes.get('auth-register-btn').hidden);
 ({nodes}=await authMode(false));assert(nodes.get('auth-register-tab').disabled);nodes.get('auth-register-tab').onclick();assert(nodes.get('auth-confirm-group').hidden);
 ({nodes}=await authMode(false,true));assert(!nodes.get('auth-register-tab').disabled);assert(!nodes.get('auth-confirm-group').hidden);nodes.get('auth-login-tab').onclick();assert(!nodes.get('auth-login-btn').hidden);
 const pickerNodes=new Map();function elem(){return{open:false,dataset:{},children:[],showModal(){this.open=true;},close(){this.open=false;},append(...x){this.children.push(...x);},replaceChildren(){this.children=[];},setAttribute(){},classList:{add(){},remove(){},contains(){return true;}}};}
 const context={URL,URLSearchParams,AbortSignal,Auth:{tokenKey:'original'},document:{getElementById(id){if(!pickerNodes.has(id))pickerNodes.set(id,elem());return pickerNodes.get(id);},createElement:elem},location:{origin:'http://localhost',hostname:'localhost',search:'',assign(url){this.assigned=url;}},localStorage:{getItem(){return null;},setItem(){},removeItem(){}},fetch:async url=>({ok:true,json:async()=>url.endsWith('/self')?{id:'self',name:'Hiro',local_entry:true}: {hubs:[{id:'self',name:'Hiro',url:'/hub'},{id:'friend',name:'Friend',url:'https://friend.example/'}]}}),setInterval(){},clearInterval(){},setTimeout(fn){fn();},clearTimeout(){}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(ui,'js/hub_picker.js'),'utf8')+';globalThis.picker=HubPicker;',context);
 await context.picker.init();assert.equal(pickerNodes.get('hub-current-name').textContent,'Hiro');assert(!pickerNodes.get('hub-picker').open);
 while(context.picker.busy)await new Promise(resolve=>setImmediate(resolve));pickerNodes.get('hub-refresh').onclick();assert(pickerNodes.get('hub-picker').open);while(context.picker.busy)await new Promise(resolve=>setImmediate(resolve));await context.picker.refresh({manual:true});const rows=pickerNodes.get('hub-list').children;assert.equal(rows.length,2);await rows[0].onclick();assert(!pickerNodes.get('hub-picker').open);
 pickerNodes.get('auth-password').value='secret';await context.picker.choose({id:'friend',name:'Friend',url:'https://friend.example/'});assert.equal(context.location.assigned,'https://friend.example/?hub=friend');assert.equal(pickerNodes.get('auth-password').value,'');assert.equal(context.Auth.tokenKey,'orion_token:self');
 const pairContext={clearInterval(){},location:{hostname:'friend.example',protocol:'https:'},HubPicker:{connected:true,self:{id:'hub'},selected:{id:'hub'},apiPath:x=>x},Auth:{getToken:()=> 'account'},document:{addEventListener(){},getElementById(){return null;}},fetch:async(url,options)=>{assert.equal(url,'/api/v1/pair/create');assert.equal(options.headers.Authorization,'Bearer account');assert.equal(JSON.parse(options.body).password,'owner');return{ok:true,json:async()=>({pairing_id:'pair'})};}};
 vm.createContext(pairContext);vm.runInContext(fs.readFileSync(path.join(ui,'js/device_pairing.js'),'utf8')+';globalThis.pair=DevicePairing;',pairContext);assert(pairContext.pair.canCreate());assert(!pairContext.pair.available());assert.equal((await pairContext.pair.createRemote('owner')).pairing_id,'pair');pairContext.location.protocol='http:';await assert.rejects(pairContext.pair.createRemote('owner'));
 pairContext.location.protocol='https:';const fields=new Map();pairContext.pair.dialog={open:false,querySelector(selector){if(!fields.has(selector))fields.set(selector,{hidden:false,focus(){},removeAttribute(){}});return fields.get(selector);},showModal(){this.open=true;}};pairContext.pair.perform=async action=>action();let attempted;pairContext.pair.generate=async(epoch,password)=>{attempted=password;};await pairContext.pair.open();assert.equal(attempted,'orion');assert(fields.get('[data-owner]').hidden);pairContext.pair.generate=async()=>{const error=new Error('wrong');error.status=403;throw error;};await pairContext.pair.open();assert(!fields.get('[data-owner]').hidden);assert.equal(pairContext.pair.usedDefault,false);
 console.log('Auth tabs, confirmation, invitation login, side-by-side discovery and scoped remote QR passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
