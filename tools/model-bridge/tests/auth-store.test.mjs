import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ProjectCredentials} from '../auth-store.mjs';
const fake={type:'oauth',access:'synthetic-access',refresh:'synthetic-refresh',expires:1};
async function store(){return new ProjectCredentials(join(await mkdtemp(join(tmpdir(),'xunqi-auth-')),'auth.json'));}
test('lists metadata only and rejects unknown provider',async()=>{
 const s=await store();await s.modify('openai-codex',async()=>fake);
 assert.deepEqual(await s.list(),[{providerId:'openai-codex',type:'oauth'}]);
 assert.equal((await s.read('openai-codex')).access,fake.access);
 await assert.rejects(s.read('other'));
});
test('two store instances serialize refresh and observe the latest credential',async()=>{
 const s=await store();const other=new ProjectCredentials(s.path);await s.modify('openai-codex',async()=>fake);
 const seen=[];
 await Promise.all([s.modify('openai-codex',async old=>{seen.push(old.expires);await new Promise(r=>setTimeout(r,50));return {...old,expires:2};}),other.modify('openai-codex',async old=>{seen.push(old.expires);return {...old,expires:old.expires+1};})]);
 assert.deepEqual(seen,[1,2]);assert.equal((await other.read('openai-codex')).expires,3);
});
test('aborted or failed refresh preserves original credentials',async()=>{
 const s=await store();await s.modify('openai-codex',async()=>fake);const c=new AbortController();
 await assert.rejects(s.modify('openai-codex',async old=>{c.abort();return {...old,access:'wrong'};},{signal:c.signal}));
 await assert.rejects(s.modify('openai-codex',async()=>{throw Error('fake failure');}));
 assert.equal((await s.read('openai-codex')).access,fake.access);
 const data=JSON.parse(await readFile(s.path,'utf8'));assert.equal(data['openai-codex'].refresh,fake.refresh);
});
