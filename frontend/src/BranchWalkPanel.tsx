import {useEffect,useRef,useState} from 'react';
import {branchWalkBatch,branchWalkRoute,cancelBranchWalk} from './api';
import {timeText} from './format';
import type {RecallResponse,BranchWalkBatchResponse,BranchWalkPair,BranchWalkRouteRequest} from './types';

function PairCard({pair,busy,onRoute,onInvalidate}:{pair:BranchWalkPair;busy:boolean;onRoute:(request:BranchWalkRouteRequest)=>void;onInvalidate:()=>void}){
 const [hotel,setHotel]=useState(pair.hotel_matching.status==='matched'?pair.hotel_matching.selected_id??'':'');
 const [restaurant,setRestaurant]=useState(pair.restaurant_matching.status==='matched'?pair.restaurant_matching.selected_id??'':'');
 const [confirmHotel,setConfirmHotel]=useState(false);const [confirmRestaurant,setConfirmRestaurant]=useState(false);
 const hexact=pair.hotel_matching.status==='matched'&&pair.hotel_matching.selected_id===hotel;
 const rexact=pair.restaurant_matching.status==='matched'&&pair.restaurant_matching.selected_id===restaurant;
 const expired=Date.now()-Date.parse(pair.queried_at)>=pair.expires_in_seconds*1000;
 const h=pair.hotels.find(p=>p.id===hotel);const r=pair.restaurants.find(p=>p.id===restaurant);
 const label='walk-'+(pair.context_id??pair.branch_id+'-'+pair.hotel_id);
 function change(){setConfirmHotel(false);setConfirmRestaurant(false);onInvalidate();}
 return <article className="branch-walk-pair" aria-label={pair.original_restaurant.name+'与'+pair.original_hotel_name+'的交通核对'}>
  <h4>{pair.original_restaurant.name} → {pair.original_hotel_name}</h4>
  <div className="original-identities"><p><strong>原道旅住宿：</strong>{pair.original_hotel_name}<br/>{pair.original_hotel_address??'地址未取得'}</p><p><strong>原召回门店：</strong>{pair.original_restaurant.name}<br/>{pair.original_restaurant.address??'地址未取得'}</p></div>
  {pair.recall_hotel&&<p className="quiet">召回时地图酒店参考：{pair.recall_hotel.name} · {pair.recall_hotel.address??'地址未知'} · {pair.recall_hotel.city??'城市未知'}；仍需与原道旅住宿核对。</p>}
  {pair.data_mode==='test' &&<p className="test-banner">测试点位 · 仅用于界面验证</p>}
  {pair.context_id&&<>
   <div className="walk-match-columns">
    <div><label htmlFor={label+'-hotel'}>住宿地图地点</label><select id={label+'-hotel'} value={hotel} disabled={busy} onChange={e=>{setHotel(e.target.value);change();}}><option value="">请选择并核对住宿</option>{pair.hotels.map(p=><option key={p.id} value={p.id}>{p.name} · {p.address??'地址未知'}</option>)}</select>
     <p className="walk-match-message">{pair.hotel_matching.explanation}</p>{h&&<p className="chosen-point">{h.name} · {h.address??'地址未知'} · {h.city??'城市未知'}</p>}
     {hotel&&!hexact&&<label className="walk-confirm"><input type="checkbox" checked={confirmHotel} disabled={busy} onChange={e=>{setConfirmHotel(e.target.checked);onInvalidate();}}/>我已核对所选地点对应原道旅住宿</label>}
    </div>
    <div><label htmlFor={label+'-restaurant'}>召回门店地图地点</label><select id={label+'-restaurant'} value={restaurant} disabled={busy} onChange={e=>{setRestaurant(e.target.value);change();}}><option value="">请选择并核对原门店</option>{pair.restaurants.map(p=><option key={p.id} value={p.id}>{p.name} · {p.address??'地址未知'}</option>)}</select>
     <p className="walk-match-message">{pair.restaurant_matching.explanation}</p>{r&&<p className="chosen-point">{r.name} · {r.address??'地址未知'} · {r.city??'城市未知'}</p>}
     {restaurant&&!rexact&&<label className="walk-confirm"><input type="checkbox" checked={confirmRestaurant} disabled={busy} onChange={e=>{setConfirmRestaurant(e.target.checked);onInvalidate();}}/>我已核对所选地点对应原召回门店</label>}
    </div>
   </div>
   {expired?<p className="stale-note">本组点位已过期，请重新核对本组点位；不能沿用旧坐标。</p>:
    <button type="button" className="button outline" disabled={busy||!hotel||!restaurant||(!hexact&&!confirmHotel)||(!rexact&&!confirmRestaurant)}
     onClick={()=>onRoute({request_id:'',context_id:pair.context_id!,hotel_option_id:hotel,restaurant_option_id:restaurant,confirm_hotel:confirmHotel,confirm_restaurant:confirmRestaurant})}>{busy?'正在核对…':(pair.walk||pair.drive)?'刷新交通路线':'确认并查询交通路线'}</button>}
  </>}
  {pair.error&&<p className="travel-error" role="status">{pair.error.message}</p>}
  {(pair.walk||pair.drive)&&<div className="verified-walk">
    <strong>餐厅→住宿交通子指标 · {pair.transport_status==='pass_walk_or_drive'?'符合步行1500米或驾车15分钟任一条件':pair.transport_status==='rejected_both_modes_over_limit'?'步行与驾车均超出限制':'路线结果尚不足以判断'}</strong>
    {pair.walk?<p>步行路线：{pair.walk.origin_name} → {pair.walk.destination_name} · {pair.walk.distance_meters}米 / {pair.walk.duration_seconds}秒 · {pair.walk_limit_status==='within_1500'?'距离≤1500米':pair.walk_limit_status==='over_1500'?'距离>1500米':'距离未核实'}</p>:<p>步行路线：未取得，不能当作超限。</p>}
    {pair.drive?<p>驾车预计：{pair.drive.origin_name} → {pair.drive.destination_name} · {pair.drive.distance_meters}米 / {pair.drive.duration_seconds}秒（约{Math.ceil(pair.drive.duration_seconds/60)}分钟） · {pair.drive_limit_status==='within_900'?'预计≤15分钟':'预计>15分钟'}</p>:<p>驾车预计：{pair.walk_limit_status==='within_1500'?'步行已达标，本轮无需追加驾车查询。':'无有效驾车结果，不能将驾车条件判为超限。'}</p>}
    <small>住宿：{pair.hotel_match_basis==='exact_fields'?'严格同店匹配':'你已核对确认'}；门店：{pair.restaurant_match_basis==='exact_fields'?'严格同店匹配':'你已核对确认'}</small>
    <small>地图路线不包含打车接驾/候车，也不保证未来时段路况、夜间通道或营业；住宿独卫、库存及费用仍未核实。</small>
   </div>}
 </article>;
}

export default function BranchWalkPanel({data}:{data:RecallResponse}){
 const [batch,setBatch]=useState<BranchWalkBatchResponse|null>(null);const [offset,setOffset]=useState(0);
 const [loading,setLoading]=useState(false);const [error,setError]=useState('');
 const [,tick]=useState(0);const version=useRef(0);const active=useRef<AbortController|null>(null);
 const operation=useRef<string|null>(null);
 useEffect(()=>{
  const timer=window.setInterval(()=>tick(v=>v+1),15000);
  return ()=>{version.current++;active.current?.abort();window.clearInterval(timer);if(operation.current)void cancelBranchWalk(operation.current).catch(()=>{});};
 },[]);
 async function start(nextOffset=offset){
  if(operation.current)void cancelBranchWalk(operation.current).catch(()=>{});
  version.current++;const current=version.current;active.current?.abort();
  const controller=new AbortController();active.current=controller;
  const id=crypto.randomUUID();operation.current=id;setOffset(nextOffset);setLoading(true);setBatch(null);setError('');
  const timer=window.setTimeout(()=>{controller.abort();void cancelBranchWalk(id).catch(()=>{});},65000);
  try{
   const result=await branchWalkBatch({request_id:data.request_id,operation_id:id,offset:nextOffset},controller.signal);
   if(version.current!==current||controller.signal.aborted)return;
   if(result.request_id!==data.request_id||result.operation_id!==id||result.provider!==data.map_provider)throw new Error('核对条件已变化，请重新召回。');
   if(result.pairs.some(p=>!data.branches.some(b=>b.id===p.branch_id&&b.hotel_ids.includes(p.hotel_id))))throw new Error('返回关联不属于本次召回，请重新召回。');
   setBatch(result);
  }catch(reason){if(version.current===current)setError(reason instanceof Error?reason.message:'本轮核对未完成，请重试。');}
  finally{window.clearTimeout(timer);if(version.current===current)setLoading(false);}
 }
 async function route(request:BranchWalkRouteRequest){
  const expected=batch?.pairs.find(p=>p.context_id===request.context_id);
  const current=++version.current;active.current?.abort();const controller=new AbortController();active.current=controller;
  setLoading(true);setError('');invalidate(request.context_id);
  const timer=window.setTimeout(()=>{controller.abort();if(operation.current)void cancelBranchWalk(operation.current).catch(()=>{});},65000);
  try{
   const result=await branchWalkRoute({...request,request_id:data.request_id},controller.signal);
   if(version.current!==current||controller.signal.aborted)return;
   if(result.context_id!==request.context_id||result.hotel_id!==expected?.hotel_id||result.branch_id!==expected?.branch_id)throw new Error('返回点位与本组不一致，请重新核对。');
   setBatch(old=>old?{...old,pairs:old.pairs.map(p=>p.context_id===result.context_id?result:p),map_http_requests:Math.max(old.map_http_requests,result.source_http_requests)}:old);
  }catch(reason){
   if(version.current!==current)return;
   const message=reason instanceof Error?reason.message:'路线未取得，请重新检索。';
   setError(message);setBatch(old=>old?{...old,pairs:old.pairs.map(p=>p.context_id===request.context_id?{...p,walk:null,walk_limit_status:'unverified',drive:null,drive_limit_status:'unverified',transport_status:'unknown_or_partial',status:'unavailable',error:{code:'ROUTE_FAILED',message}}:p)}:old);
  }finally{window.clearTimeout(timer);if(version.current===current)setLoading(false);}
 }
 function invalidate(id:string){setBatch(old=>old?{...old,pairs:old.pairs.map(p=>p.context_id===id?{...p,walk:null,walk_limit_status:'unverified',drive:null,drive_limit_status:'unverified',transport_status:'unknown_or_partial',error:null,status:'needs_confirmation'}:p)}:old);}
 function stop(){
  version.current++;active.current?.abort();if(operation.current)void cancelBranchWalk(operation.current).catch(()=>{});
  operation.current=null;setBatch(null);setLoading(false);setError('已请求停止；正在进行的请求仍可能在结束。');
 }
 return <section className="branch-walk-panel" aria-label="召回关联的批量位置与交通核对">
  <div className="recall-heading"><h3>把关联，核对成路线</h3><span>{data.map_provider==='amap'?'高德地图':'腾讯地图'} · 固定本次来源</span></div>
  <p>从整次召回关联轮转选组，每轮最多3组；列表筛选不会改变范围。住宿和原堂食门店都确认后查步行路线，距离超过1500米才追加同地图驾车预计（≤15分钟通过）；都未知不能标为合格。</p>
  <button type="button" className="button outline" disabled={loading||!data.walk_check_available} onClick={()=>void start()}>{batch?'重新核对本组点位':'批量核对位置与交通（最多3组）'}</button>
  {loading&&<button type="button" className="text-button" onClick={stop}>停止本轮核对</button>}
  {!data.walk_check_available&&<p className="stale-note">本次召回关联不可用或来自旧会话，请重新召回后核对。</p>}
  {loading&&<p role="status"><span className="spinner"/>正在解析点位或查询路线，歧义项不会自动查路线。</p>}
  {error&&<p className="travel-error" role="alert">{error}</p>}
  {batch&&<>
   {batch.data_mode==='test'&&<p className="test-banner">测试数据 · 仅用于核对流程验证</p>}
   <p className="walk-batch-summary">第{offset+1}至{Math.min(offset+3,batch.total_pairs)}组 / 共{batch.total_pairs}组关联；已取得路线{batch.pairs.filter(p=>p.walk!==null||p.drive!==null).length}组，待确认{batch.pairs.filter(p=>p.status==='needs_confirmation').length}组。查询于{timeText(batch.queried_at)}</p>
   <div className="travel-warnings">{batch.warnings.map(w=><p key={w}>{w}</p>)}</div>
   {batch.error&&<p className="travel-error">{batch.error.message}</p>}
   {batch.pairs.map((pair,index)=><PairCard key={batch.operation_id+'-'+index} pair={pair} busy={loading} onRoute={request=>void route(request)} onInvalidate={()=>pair.context_id&&invalidate(pair.context_id)}/>)}
   <div className="walk-pagination">{offset>0&&<button type="button" className="text-button" disabled={loading} onClick={()=>void start(Math.max(0,offset-3))}>重新核对上一组</button>}
    {batch.next_offset!==null&&<button type="button" className="text-button" disabled={loading} onClick={()=>void start(batch.next_offset!)}>核对下一组（会新查地图）</button>}</div>
   <p className="quiet">切换组会停止旧核对并重新查源；点位过期后不沿用旧确认。路线不是夜间可达或全部住宿条件的保证。</p>
  </>}
 </section>;
}
