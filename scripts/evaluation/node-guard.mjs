// Wrap the project's actual bridge, without altering production provider code.
import {execFileSync} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {dirname,resolve} from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const ledger=resolve(root,'scripts/evaluation/ledger.py');
const python='D:/Anaconda3/envs/xunqi/python.exe';
const db=process.argv[2];
if(!db)throw Error('LEDGER_REQUIRED');
const command=(...args)=>execFileSync(python,[ledger,...args],{encoding:'utf8',windowsHide:true,stdio:['ignore','pipe','ignore']});
const fetch=globalThis.fetch;
globalThis.fetch=async(input,init)=>{
  const url=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url);
  if(url.protocol!=='https:'||url.hostname!=='api.deepseek.com'||url.pathname!=='/chat/completions'||url.port)throw Error('ENDPOINT_DENIED');
  const id=command('reserve',db,'deepseek').trim();
  const response=await fetch(input,{...init,redirect:'error'});
  command('finish',db,id,String(response.status));
  return response;
};
await import(pathToFileURL(resolve(root,'tools/model-bridge/bridge.mjs')).href);
