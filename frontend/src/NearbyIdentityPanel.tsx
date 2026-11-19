import {useEffect,useRef,useState} from 'react';
import {confirmNearbyIdentity,nearbyIdentityStatus,revokeNearbyIdentity} from './api';
import NearbyIdentityRooms from './NearbyIdentityRooms';
import {money,timeText} from './format';
import type {Hotel,NearbyHotelOption,NearbyIdentityResponse,SearchInput} from './types';

type Props={contextId:string;anchorId:string;requestId:string;point:NearbyHotelOption;sourceHotels:Hotel[];
 query:SearchInput;expiresAt:number;disabled:boolean};
const operationId=()=>crypto.randomUUID().replaceAll('-','');
const message=(reason:unknown)=>reason instanceof Error?reason.message:'本地核对未完成，请同步状态后重试。';

export default function NearbyIdentityPanel(props:Props){
 const {contextId,anchorId,requestId,point,sourceHotels,query,expiresAt,disabled}=props;
 const scope={context_id:contextId,anchor_id:anchorId,request_id:requestId};
 const scopeKey=JSON.stringify([scope,query,point.id,point.name,point.address,sourceHotels.map(h=>[h.id,h.name,h.address])]);
 const [open,setOpen]=useState(false),[state,setState]=useState<NearbyIdentityResponse|null>(null);
 const [stateKey,setStateKey]=useState(''),[validUntil,setValidUntil]=useState(0);
 const [selection,setSelection]=useState(''),[acknowledged,setAcknowledged]=useState(false);
 const [busy,setBusy]=useState<'status'|'confirm'|'revoke'|null>(null),[error,setError]=useState('');
 const [suppressed,setSuppressed]=useState(false),[,tick]=useState(0);
 const epoch=useRef(0),sequence=useRef(0),active=useRef<AbortController|null>(null);
 const target=useRef<{operationId:string;hotelId:number}|null>(null);
 const pendingRevoke=useRef<{operationId:string;hotelId:number}|null>(null);
 useEffect(()=>{
  epoch.current++;active.current?.abort();target.current=null;pendingRevoke.current=null;
  setOpen(false);setState(null);setStateKey('');setValidUntil(0);setSelection('');setAcknowledged(false);
  setBusy(null);setError('');setSuppressed(false);
  return ()=>{epoch.current++;active.current?.abort();};
 },[scopeKey]);
 useEffect(()=>{if(!open)return;const timer=window.setInterval(()=>tick(n=>n+1),1000);return ()=>window.clearInterval(timer);},[open]);
 const fresh=Date.now()<Math.min(expiresAt,validUntil)&&stateKey===scopeKey;
 const pairs=(fresh?state?.eligible_pairs:[])?.filter(p=>p.hotel_option_id===point.id&&p.map_name===point.name&&p.map_address===point.address&&p.map_city===point.city&&sourceHotels.some(h=>h.id===p.source_hotel_id&&h.name===p.source_name&&h.address===p.source_address))??[];
 const binding=fresh&&!suppressed?state?.bindings.find(b=>b.hotel_option_id===point.id&&b.identity_basis==='user_confirmed'):undefined;
 const currentPair=pairs.find(p=>p.source_hotel_id===(binding?.source_hotel_id??Number(selection)));
 const source=binding&&currentPair?sourceHotels.find(h=>h.id===binding.source_hotel_id):undefined;
 const price=source?.currency==='CNY'&&source.reference_price_fen!==null&&Number.isSafeInteger(source.reference_price_fen)&&source.reference_price_fen>=0?source.reference_price_fen:null;
 function validate(response:NearbyIdentityResponse){
  if(response.status!=='complete'||response.context_id!==contextId||response.anchor_id!==anchorId||response.request_id!==requestId)throw new Error('本轮核对上下文已变化，请重新选择地点。');
  return response;
 }
 function accept(response:NearbyIdentityResponse,started:number){
  validate(response);sequence.current=Math.max(sequence.current,response.intent_seq);
  setState(response);setStateKey(scopeKey);
  setValidUntil(old=>Math.min(old||Infinity,expiresAt,started+response.expires_in_seconds*1000));
 }
 async function refresh(){
  if(disabled||busy||Date.now()>=expiresAt)return;
  // A sync cannot cancel the user's outstanding revocation intent.
  if(pendingRevoke.current){await revoke();return;}
  const version=++epoch.current,controller=new AbortController();active.current?.abort();active.current=controller;
  const started=Date.now();setOpen(true);setBusy('status');setError('');setAcknowledged(false);setSelection('');
  try{
   const response=validate(await nearbyIdentityStatus(scope,controller.signal));
   if(version!==epoch.current||controller.signal.aborted)return;
   accept(response,started);setSuppressed(false);
   const existing=response.bindings.find(b=>b.hotel_option_id===point.id);
   target.current=existing?{operationId:existing.operation_id,hotelId:existing.source_hotel_id}:null;
  }catch(reason){if(version===epoch.current){setSuppressed(true);setError(message(reason));}}
  finally{if(version===epoch.current)setBusy(null);}
 }
 async function confirm(){
  if(!fresh||!currentPair||!acknowledged||busy||disabled||pendingRevoke.current)return;
  const version=++epoch.current,controller=new AbortController();active.current=controller;
  const id=operationId(),started=Date.now();sequence.current=Math.max(sequence.current,state?.intent_seq??0)+1;
  target.current={operationId:id,hotelId:currentPair.source_hotel_id};
  setBusy('confirm');setSuppressed(true);setError('');
  try{
   const response=await confirmNearbyIdentity({...scope,hotel_option_id:point.id,source_hotel_id:currentPair.source_hotel_id,
    operation_id:id,intent_seq:sequence.current,expected_revision:state!.revision,confirmed:true},controller.signal);
   if(version!==epoch.current||controller.signal.aborted)return;
   accept(response,started);setSuppressed(false);
  }catch(reason){if(version===epoch.current){setError(message(reason));setAcknowledged(false);}}
  finally{if(version===epoch.current)setBusy(null);}
 }
 async function revoke(){
  const intended=pendingRevoke.current??target.current;
  if(!intended||busy==='revoke'||disabled)return;
  pendingRevoke.current=intended;
  const version=++epoch.current,controller=new AbortController();active.current?.abort();active.current=controller;
  const started=Date.now();setSuppressed(true);setAcknowledged(false);setBusy('revoke');setError('');
  async function send(revision:number){
   sequence.current++;
   return revokeNearbyIdentity({...scope,hotel_option_id:point.id,source_hotel_id:intended!.hotelId,
    operation_id:operationId(),target_operation_id:intended!.operationId,
    intent_seq:sequence.current,expected_revision:revision},controller.signal);
  }
  try{
   let response:NearbyIdentityResponse;
   try{response=await send(state?.revision??0);}
   catch(reason){
    if((reason as {status?:number})?.status!==409)throw reason;
    const latest=validate(await nearbyIdentityStatus(scope,controller.signal));
    if(version!==epoch.current||controller.signal.aborted)return;
    const live=latest.bindings.find(b=>b.hotel_option_id===point.id);
    if(live&&(live.operation_id!==intended.operationId||live.source_hotel_id!==intended.hotelId))throw new Error('确认关系已变化，本次未撤销新的确认；价格保持隐藏，请重新核对。');
    sequence.current=Math.max(sequence.current,latest.intent_seq);
    response=await send(latest.revision);
   }
   if(version!==epoch.current||controller.signal.aborted)return;
   accept(response,started);target.current=null;pendingRevoke.current=null;setSelection('');
  }catch(reason){if(version===epoch.current)setError(message(reason));}
  finally{if(version===epoch.current)setBusy(null);}
 }
 async function beforeRooms(signal:AbortSignal){
  const version=epoch.current,started=Date.now();
  try{
   if(!fresh||Date.now()>=Math.min(expiresAt,validUntil)||!binding||!currentPair||pendingRevoke.current)throw new Error('同店关联已过期或撤销，请重新查询后核对。');
   const response=validate(await nearbyIdentityStatus(scope,signal));
   if(version!==epoch.current||signal.aborted)throw new Error('本轮核对操作已变化。');
   const live=response.bindings.find(b=>b.hotel_option_id===point.id);
   const pair=response.eligible_pairs.find(p=>p.hotel_option_id===point.id&&p.source_hotel_id===binding.source_hotel_id);
   if(response.expires_in_seconds<=0||!live||live.operation_id!==binding.operation_id||live.source_hotel_id!==binding.source_hotel_id||!pair||JSON.stringify(pair)!==JSON.stringify(currentPair))throw new Error('本轮来源身份或确认关系已变化，请重新核对。');
   if(Date.now()>=Math.min(expiresAt,validUntil,started+response.expires_in_seconds*1000))throw new Error('同店关联已过期，请重新查询后核对。');
   accept(response,started);
  }catch(reason){
   if(version===epoch.current){setSuppressed(true);setError(message(reason));}
   throw reason;
  }
 }
 return <div className="nearby-manual-identity" aria-label={point.name+'的人工同店核对'}>
  {!open?<button type="button" disabled={disabled||Date.now()>=expiresAt} onClick={()=>void refresh()}>人工核对同店（仅补城市缺失）</button>:<>
   <p className="manual-identity-note">住宿来源未独立提供城市；这次同店身份由你核对确认。仅本轮有效，不进入价格、口碑或综合排名。</p>
   <div className="manual-identity-columns">
    <div><small>地图酒店</small><strong>{point.name}</strong><p>{point.address}</p><p>地图城市：{point.city}</p></div>
    <div><small>住宿来源酒店</small>{currentPair?<><strong>{currentPair.source_name}</strong><p>{currentPair.source_address}</p><p>住宿来源城市：未知（本轮原响应缺失）</p><p>道旅 · 原观察时间 <time dateTime={currentPair.source_observed_at}>{new Date(currentPair.source_observed_at).toLocaleString('zh-CN',{hour12:false})}</time></p></>:<p>请手动选择本轮可核对的酒店</p>}</div>
   </div>
   {source?<>
    <strong className="manual-identity-confirmed">你已确认同店 · 仅本轮有效</strong>
    <p>道旅原搜索参考价 {price!==null?<strong>¥{money(price)}</strong>:'未知'} · {currentPair&&timeText(currentPair.source_observed_at)} · 非最终含费价</p>
    <NearbyIdentityRooms key={binding!.operation_id} requestId={requestId} hotelId={source.id} query={query} beforeLoad={beforeRooms}/>
   </>:<>
    {fresh&&pairs.length>0&&<>
     <label>本轮住宿来源酒店<select aria-label="本轮住宿来源酒店" disabled={!!busy} value={selection} onChange={event=>{setSelection(event.target.value);setAcknowledged(false);}}>
      <option value="">请选择酒店</option>{pairs.map(p=><option value={p.source_hotel_id} key={p.source_hotel_id}>{p.source_name} · {p.source_address}</option>)}
     </select></label>
     <label className="manual-identity-ack"><input type="checkbox" checked={acknowledged} disabled={!selection||!!busy} onChange={event=>setAcknowledged(event.target.checked)}/>我已核对两侧名称、地址和城市，确认是同一家酒店</label>
     <button type="button" disabled={!selection||!acknowledged||!!busy||disabled||!!pendingRevoke.current} onClick={()=>void confirm()}>{busy==='confirm'?'正在本地确认…':'确认同店'}</button>
    </>}
    {!fresh&&state&&<p>本轮同店身份已过期或条件已变，请重新查询并核对；没有自动查源。</p>}
    {fresh&&!pairs.length&&<p>{state?.reason}</p>}
   </>}
   {(target.current||binding)&&<button type="button" disabled={busy==='revoke'||disabled||Date.now()>=expiresAt} onClick={()=>void revoke()}>{busy==='revoke'?'正在本地撤销…':'撤销本次同店确认'}</button>}
   {pendingRevoke.current&&busy!=='revoke'&&<p>撤销尚未完成；价格和房型入口保持隐藏，可同步状态继续撤销原确认。</p>}
   <button type="button" disabled={!!busy||disabled||Date.now()>=expiresAt} onClick={()=>void refresh()}>同步本轮状态（仅本地）</button>
   {error&&<p role="alert">{error}</p>}
  </>}
 </div>;
}
