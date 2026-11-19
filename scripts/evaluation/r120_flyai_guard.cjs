// Meter the official CLI's one authorized POST without logging credentials or wire data.
const {execFileSync}=require('node:child_process');
const env=process.env;
const fetchOriginal=globalThis.fetch;
const deny=()=>{throw Error('ENDPOINT_DENIED');};
for(const protocol of ['node:http','node:https']){
  const client=require(protocol);
  client.request=client.get=deny;
}
const meter=(...args)=>execFileSync(env.XUNQI_EVAL_PYTHON,[env.XUNQI_EVAL_LEDGER,...args],{
  encoding:'utf8',windowsHide:true,stdio:['ignore','pipe','ignore'],
});
globalThis.fetch=async(input,options)=>{
  const url=new URL(input instanceof Request?input.url:String(input));
  const method=options?.method??(input instanceof Request?input.method:null);
  if(url.href!=='https://flyai.open.fliggy.com/mcp'||method!=='POST')return deny();
  const id=meter('reserve',env.XUNQI_EVAL_DB,'flyai').trim();
  const response=await fetchOriginal(input,{...options,redirect:'error'});
  meter('finish',env.XUNQI_EVAL_DB,id,String(response.status));
  return response;
};
