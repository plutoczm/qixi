import {useEffect,useRef,useState} from 'react';
import {hotelQuotes} from './api';
import {BookabilityGate} from './CapabilityStages';
import QuoteEvidence from './QuoteEvidence';
import RoomQuoteRow from './RoomQuoteRow';
import type {QuotesResponse,SearchInput} from './types';

const queryKey=(query:SearchInput)=>JSON.stringify(Object.entries(query).sort(([a],[b])=>a.localeCompare(b)));

/** Mounted only while the parent has a fresh, manually confirmed identity. */
export default function NearbyIdentityRooms({requestId,hotelId,query,beforeLoad}:{requestId:string;hotelId:number;query:SearchInput;beforeLoad:(signal:AbortSignal)=>Promise<void>}){
 const [data,setData]=useState<QuotesResponse|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0),active=useRef<AbortController|null>(null);
 useEffect(()=>()=>{generation.current++;active.current?.abort();},[]);
 async function load(){
  if(busy)return;
  const version=++generation.current,controller=new AbortController();active.current=controller;
  setBusy(true);setError('');setData(null);
  const timer=window.setTimeout(()=>controller.abort(),65000);
  try{
   await beforeLoad(controller.signal);
   if(version!==generation.current||controller.signal.aborted)return;
   const response=await hotelQuotes(hotelId,requestId,controller.signal);
   if(version!==generation.current||controller.signal.aborted)return;
   if(response.request_id!==requestId||response.hotel_id!==hotelId||queryKey(response.context)!==queryKey(query))throw new Error('房型所属条件已变化，请重新核对。');
   if(response.status==='unavailable')throw new Error(response.error?.message??'本次未取得房型参考。');
   await beforeLoad(controller.signal);
   if(version!==generation.current||controller.signal.aborted)return;
   setData(response);
  }catch(reason){
   if(version===generation.current)setError(controller.signal.aborted?'房型查询已停止或超时，没有自动重试。':reason instanceof Error?reason.message:'本次未取得房型参考。');
  }finally{window.clearTimeout(timer);if(version===generation.current)setBusy(false);}
 }
 return <div className="manual-identity-rooms">
  <button type="button" disabled={busy} onClick={()=>void load()}>{busy?'房型查询中…':'查看这家酒店房型（需另行查源）'}</button>
  {error&&<p role="alert">{error}</p>}
  {data&&<section aria-label="人工确认酒店的房型参考">
   <p>本次请求 {data.context.adults} 成人、{data.context.rooms} 间房；{data.occupancy_status==='matched'?'来源已回显入住组合，仍须核实独卫、库存和最终费用。':'来源未完整核实入住组合，不能由价格推断可住或可订。'}</p>
   <BookabilityGate data={data}/><QuoteEvidence data={data}/>
   <p>同店身份来自你的本轮确认；房型为来源展示参考，完整费用、足量库存与夜间条件仍须另行核实。</p>
   {data.quotes.slice(0,20).map((quote,index)=><RoomQuoteRow key={quote.id} quote={quote} context={data.context} index={index} sourceName={data.source.name}/>)}
   {!data.quotes.length&&<p>本次未返回房型参考。</p>}
   {data.quotes.length>20&&<p>此处只展示前20条，可在本轮住宿来源列表查看全部房型。</p>}
  </section>}
 </div>;
}
