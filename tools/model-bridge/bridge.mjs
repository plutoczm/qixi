import {createModels} from '@earendil-works/pi-ai/models';
import {deepseekProvider} from '@earendil-works/pi-ai/providers/deepseek';
import {openaiProvider} from '@earendil-works/pi-ai/providers/openai';
import {ProjectCredentials} from './auth-store.mjs';
import {classifyProviderHttpError} from './provider-errors.mjs';
import {fileURLToPath} from 'node:url';
import {dirname,resolve} from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const credentials=new ProjectCredentials(resolve(root,'runtime/model-auth/auth.json'));
const originalFetch=globalThis.fetch;
let active;let started=false;let finished=false;let buffer='';
let lastUpstreamErrorStatus=null;
const emit=data=>process.stdout.write(JSON.stringify(data)+'\n');
function fail(code){if(finished)return;finished=true;emit({type:'failed',code});active?.abort();setTimeout(()=>process.exit(0),25);}
function guardedFetch(input,init={}){
 const url=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url);
 const allowed=(url.hostname==='api.deepseek.com'&&url.pathname==='/chat/completions')
  ||(url.hostname==='api.openai.com'&&url.pathname==='/v1/responses')
  ||(url.hostname==='auth.openai.com'&&url.pathname==='/api/accounts/oauth/token');
 if(url.protocol!=='https:'||!allowed||url.username||url.password||url.port)throw Error('ENDPOINT_DENIED');
 return originalFetch(input,{...init,redirect:'error',signal:AbortSignal.any([...(init.signal?[init.signal]:[]),active.signal])})
  .then(response=>{if(url.hostname==='api.deepseek.com'&&!response.ok)lastUpstreamErrorStatus=response.status;return response;});
}
async function generate(request){
 if(started){fail('PROTOCOL_ERROR');return;}started=true;
 const allowed=new Set(['type','provider','model','system','prompt','api_key','schema','max_tokens']);
 if(Object.keys(request).some(k=>!allowed.has(k))||!['deepseek','pi_chatgpt'].includes(request.provider)||typeof request.model!=='string'||typeof request.system!=='string'||typeof request.prompt!=='string'||request.system.length>12000||request.prompt.length>20000){fail('PROTOCOL_ERROR');return;}
 active=new AbortController();lastUpstreamErrorStatus=null;let timedOut=false;
 const timeout=setTimeout(()=>{timedOut=true;active.abort();},60000);
 let httpStatus=null;let bytes=0;let reportedProgress=false;
 try{
  globalThis.fetch=guardedFetch;
  const models=createModels({...(request.provider==='pi_chatgpt'?{credentials}:{}),authContext:{env:async()=>undefined,fileExists:async()=>false}});
  const provider=request.provider==='deepseek'?deepseekProvider():openaiProvider();
  models.setProvider(provider);
  const model=models.getModel(provider.id,request.model);
  if(!model)throw Error('UNKNOWN_MODEL');
  if(request.provider==='deepseek'&&(request.model!=='deepseek-flash'||typeof request.api_key!=='string'||!request.api_key))throw Error('INVALID_CONFIGURATION');
  if(request.provider==='pi_chatgpt'&&request.api_key!==undefined)throw Error('INVALID_CONFIGURATION');
  const options={signal:active.signal,maxTokens:Math.min(4096,Math.max(256,request.max_tokens??2048)),maxRetries:0,timeoutMs:20000,transport:'sse',cacheRetention:'none',fetch:guardedFetch,
   onResponse:r=>{httpStatus=r.status;},
   onPayload:payload=>{
    if(request.provider==='deepseek')return {...payload,response_format:{type:'json_object'},thinking:{type:'disabled'}};
    const text={...payload.text};
    if(request.schema)text.format={type:'json_schema',name:'conditions',strict:true,schema:request.schema};
    return {...payload,store:false,text,max_output_tokens:Math.min(4096,request.max_tokens??2048)};
   }
  };
  if(request.provider==='deepseek')options.apiKey=request.api_key;
  else {options.reasoningEffort='none';options.reasoningSummary='off';}
  const stream=models.stream(model,{systemPrompt:request.system,messages:[{role:'user',content:request.prompt,timestamp:Date.now()}]},options);
  for await(const event of stream){
   if(event.type==='start')emit({type:'started'});
   if(event.type==='text_delta'||event.type==='thinking_delta'){bytes+=Buffer.byteLength(event.delta??'');if(bytes>65536){active.abort();throw Error('OUTPUT_LIMIT');}}
   if(event.type==='text_delta'&&!reportedProgress&&bytes>0){reportedProgress=true;emit({type:'progress',output_bytes:bytes});}
  }
  const result=await stream.result();
  if(active.signal.aborted){fail(timedOut?'TIMEOUT':'CANCELLED');return;}
  if(result.stopReason!=='stop'){
   const code=result.stopReason==='length'?'OUTPUT_TRUNCATED':result.stopReason==='toolUse'?'UNEXPECTED_TOOL_CALL':classifyProviderHttpError(request.provider,lastUpstreamErrorStatus??httpStatus);
   fail(code);return;
  }
  const text=result.content.filter(c=>c.type==='text').map(c=>c.text).join('');
  if(Buffer.byteLength(text)>32768){fail('OUTPUT_LIMIT');return;}
  const sensitive=request.provider==='deepseek'?[request.api_key]:Object.values((await credentials.read('openai'))??{}).filter(v=>typeof v==='string'&&v.length>40);
  if(sensitive.some(value=>text.includes(value))){fail('SECRET_EXPOSURE_BLOCKED');return;}
  if(finished)return;
  const usage={};for(const name of ['input','output','cacheRead','totalTokens'])if(Number.isSafeInteger(result.usage?.[name])&&result.usage[name]>=0)usage[name]=result.usage[name];
  finished=true;emit({type:'completed',provider:request.provider,model:request.model,text,usage,format:request.provider==='deepseek'?'json_object':request.schema?'json_schema':'validated_json'});
 }catch(error){
  const known=['UNKNOWN_MODEL','INVALID_CONFIGURATION','ENDPOINT_DENIED','OUTPUT_LIMIT'];
  fail(active.signal.aborted?(timedOut?'TIMEOUT':'CANCELLED'):known.includes(error.message)?error.message:classifyProviderHttpError(request.provider,lastUpstreamErrorStatus??httpStatus));
 }finally{clearTimeout(timeout);setTimeout(()=>process.exit(0),25);}
}
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>{
 buffer+=chunk;
 if(Buffer.byteLength(buffer)>65536){fail('REQUEST_LIMIT');return;}
 let at;
 while((at=buffer.indexOf('\n'))>=0){
  const line=buffer.slice(0,at);buffer=buffer.slice(at+1);
  try{const request=JSON.parse(line);if(request.type==='cancel'){if(!active){fail('CANCELLED');return;}active.abort();}else if(request.type==='generate')void generate(request);else fail('PROTOCOL_ERROR');}
  catch{fail('PROTOCOL_ERROR');}
 }
});
process.stdin.on('end',()=>{if(!finished){active?.abort();fail('CANCELLED');}});
process.on('SIGINT',()=>{active?.abort();fail('CANCELLED');});
