// The FlyAI hotel CLI can send one POST to its official endpoint.
const originalFetch=globalThis.fetch;
let sent=false;
const deny=()=>{throw Error('FLYAI_NETWORK_DENIED');};
for(const protocol of ['node:http','node:https']){
  const transport=require(protocol);
  transport.request=transport.get=deny;
}
globalThis.fetch=async(input,options)=>{
  const url=new URL(input instanceof Request?input.url:String(input));
  const method=options?.method??(input instanceof Request?input.method:null);
  if(sent||url.href!=='https://flyai.open.fliggy.com/mcp'||method!=='POST')return deny();
  sent=true;
  return originalFetch(input,{...options,redirect:'error'});
};
