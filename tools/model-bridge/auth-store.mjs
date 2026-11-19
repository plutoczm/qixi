import {mkdir,open,readFile,rename,stat,unlink} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as sleep} from 'node:timers/promises';
export class ProjectCredentials {
 constructor(path){this.path=resolve(path);}
 valid(id){if(!['openai-codex','openai'].includes(id))throw Error('UNKNOWN_AUTH_PROVIDER');}
 async load(){
  try{const info=await stat(this.path);if(!info.isFile()||info.size>65536)throw Error('AUTH_STORAGE_INVALID');const data=JSON.parse(await readFile(this.path,'utf8'));if(!data||typeof data!=='object'||Array.isArray(data))throw Error('AUTH_STORAGE_INVALID');return data;}
  catch(e){if(e.code==='ENOENT')return {};throw Error('AUTH_STORAGE_UNAVAILABLE');}
 }
 async read(id,options={}){this.valid(id);options.signal?.throwIfAborted();return (await this.load())[id];}
 async list(options={}){options.signal?.throwIfAborted();const data=await this.load();return ['openai-codex','openai'].filter(id=>data[id]?.type==='oauth').map(providerId=>({providerId,type:'oauth'}));}
 async locked(fn,options={}){
  await mkdir(dirname(this.path),{recursive:true});
  const lock=this.path+'.lock';const until=Date.now()+30000;let handle;
  while(!handle){
   options.signal?.throwIfAborted();
   try{handle=await open(lock,'wx',0o600);}catch(e){if(e.code!=='EEXIST')throw Error('AUTH_STORAGE_UNAVAILABLE');if(Date.now()>=until)throw Error('AUTH_STORAGE_BUSY');await sleep(50,undefined,{signal:options.signal});}
  }
  try{return await fn();}finally{await handle.close();await unlink(lock);}
 }
 async save(data,signal){
  signal?.throwIfAborted();
  const temp=this.path+'.'+randomUUID()+'.tmp';
  try{const file=await open(temp,'wx',0o600);try{await file.writeFile(JSON.stringify(data),'utf8');await file.sync();}finally{await file.close();}
   signal?.throwIfAborted();await rename(temp,this.path);
  }finally{await unlink(temp).catch(e=>{if(e.code!=='ENOENT')throw Error('AUTH_STORAGE_UNAVAILABLE');});}
 }
 async modify(id,fn,options={}){
  this.valid(id);
  return this.locked(async()=>{const data=await this.load();options.signal?.throwIfAborted();const next=await fn(data[id]);options.signal?.throwIfAborted();if(next!==undefined){if(next.type!=='oauth'||typeof next.access!=='string'||typeof next.refresh!=='string'||!Number.isFinite(next.expires))throw Error('AUTH_STORAGE_INVALID');data[id]=next;await this.save(data,options.signal);}return data[id];},options);
 }
 async delete(id,options={}){
  this.valid(id);return this.locked(async()=>{const data=await this.load();delete data[id];await this.save(data,options.signal);},options);
 }
}
