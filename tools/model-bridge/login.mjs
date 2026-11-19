import {createModels} from '@earendil-works/pi-ai/models';
import {openaiProvider} from '@earendil-works/pi-ai/providers/openai';
import {randomUUID} from 'node:crypto';
import {ProjectCredentials} from './auth-store.mjs';
import {loginFailure} from './login-errors.mjs';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile,unlink,readFile} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const authDir=resolve(root,'runtime/model-auth');
const store=new ProjectCredentials(resolve(authDir,'auth.json'));
if(process.argv[2]==='--status'){
 console.log(JSON.stringify({provider:'pi_chatgpt',oauth_configured:(await store.list()).some(row=>row.providerId==='openai'),inference_verified:false}));process.exit(0);
}
const controller=new AbortController();
const timer=setTimeout(()=>controller.abort(),10*60*1000);
process.on('SIGINT',()=>controller.abort());
let signaledUrl=false;
try{
 await mkdir(authDir,{recursive:true});
 const models=createModels({credentials:store,authContext:{env:async()=>undefined,fileExists:async()=>false}});
 models.setProvider(openaiProvider());
 const deviceFile=resolve(authDir,'installation-id.txt');
 try{await writeFile(deviceFile,randomUUID(),{flag:'wx',mode:0o600});}catch(e){if(e.code!=='EEXIST')throw e;}
 const deviceId=(await readFile(deviceFile,'utf8')).trim();
 if(!/^[0-9a-f-]{36}$/.test(deviceId))throw Error('INVALID_INSTALLATION_ID');
 await models.login('openai','oauth',{
  signal:controller.signal,
  prompt:async prompt=>{
   if(prompt.type==='select'){
    const choice=prompt.options.find(option=>option.id==='browser');
    if(!choice)throw Error('BROWSER_LOGIN_UNAVAILABLE');
    return choice.id;
   }
   if(prompt.type!=='manual_code')throw Error('MANUAL_LOGIN_REQUIRED');
   console.log('Complete login in your browser. Authorization codes must stay on this computer.');
   return new Promise((resolve,reject)=>{
    if(controller.signal.aborted){reject(Error('LOGIN_CANCELLED'));return;}
    // Per-prompt abort means the callback won. Rejecting it calls callback.cancel()
    // while the SDK closes the callback server, causing duplicate cleanup on Windows.
    controller.signal.addEventListener('abort',()=>reject(Error('LOGIN_CANCELLED')),{once:true});
   });
  },
  notify:event=>{
   if(event.type==='auth_url'){
    const url=new URL(event.url);
    if(url.protocol!=='https:'||url.hostname!=='auth.openai.com'||url.pathname!=='/api/accounts/authorize')throw Error('UNEXPECTED_LOGIN_HOST');
    writeFile(resolve(authDir,'login-url.txt'),url.href,{mode:0o600}).then(()=>{signaledUrl=true;console.log('LOGIN_URL_READY');}).catch(()=>controller.abort());
   }else if(event.type==='device_code')throw Error('BROWSER_LOGIN_REQUIRED');
  }
 },{agentName:'Xunqi',getDeviceId:()=>deviceId});
 console.log('LOGIN_COMPLETED');
}catch(error){
 const failure=controller.signal.aborted?{reason:'CANCELLED_OR_EXPIRED',http_status:null,cause_codes:[]}:loginFailure(error);
 console.log(JSON.stringify({status:'LOGIN_FAILED',...failure}));
 process.exitCode=2;
}finally{
 clearTimeout(timer);
 if(signaledUrl)await unlink(resolve(authDir,'login-url.txt')).catch(()=>{});

}
