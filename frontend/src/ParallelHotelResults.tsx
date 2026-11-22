import {useEffect,useRef,useState} from 'react';
import {loadTuniuPage} from './api';
import HotelResults from './HotelResults';
import HotelTransport,{samePlace} from './HotelTransport';
import type {ConfirmedDestination} from './HotelTransport';
import type {TransportObservation} from './TravelPanel';
import {conflictingParallelQuote,parallelHotelAnchor,parallelHotelKey,recommendParallel,rememberParallelQuote,shortlistParallel} from './parallelRecommendations';
import type {WalkEvidence} from './parallelRecommendations';
import type {ParallelSearchResponse,ParallelSourceResult,QuotesResponse,SearchResponse} from './types';
import {emptyHotelLocalFilters,parseHotelLocalPrice,selectLocalHotels} from './hotelLocalView';
import type {HotelLocalFilters} from './hotelLocalView';
import './parallelHotelResults.css';

const labels:Record<string,string>={rollinggo:'道旅',tuniu:'途牛',flyai:'飞猪'};
const states:Record<ParallelSourceResult['status'],string>={
  complete:'查询完成',partial:'部分返回',limited:'覆盖有限',unavailable:'暂不可用',
  unconfigured:'未配置',expired:'查询超时',cancelled:'已取消',
};
const errors:Record<string,string>={
  TIMEOUT:'查询超时',DEADLINE_EXCEEDED:'查询超时',RATE_LIMITED:'来源限流',
  AUTHENTICATION_FAILED:'认证失败',ACCESS_DENIED:'访问未授权',UNSUPPORTED_PLACE_TYPE:'此来源需明确城市',
  UNSUPPORTED_REGION:'地点超出来源范围',CONFIGURATION_MISSING:'本机未配置',
  REQUEST_LIMIT:'本轮请求额度已用完',
};

export default function ParallelHotelResults({batch,walks={},requireWalk=false,destination=null}:{
  batch:ParallelSearchResponse;walks?:Readonly<Record<string,WalkEvidence>>;requireWalk?:boolean;
  destination?:ConfirmedDestination|null;
}){
  const [pages,setPages]=useState<SearchResponse|null>(null);
  const [quotes,setQuotes]=useState<Record<string,QuotesResponse>>({});
  const [transport,setTransport]=useState<Record<string,TransportObservation>>({});
  const [filters,setFilters]=useState<Record<string,HotelLocalFilters>>({});
  const [paging,setPaging]=useState(false);
  const [pageError,setPageError]=useState('');
  const [now,setNow]=useState(Date.now);
  const active=useRef<AbortController|null>(null);
  useEffect(()=>{
    const timer=window.setInterval(()=>setNow(Date.now()),1000);
    return ()=>{window.clearInterval(timer);active.current?.abort();};
  },[]);
  const sources=batch.sources.map(source=>source.id==='tuniu'&&pages
    ?{...source,search:pages,status:pages.coverage.limited?'limited' as const:pages.status}:source);
  const cardWalks:Record<string,WalkEvidence>={};
  if(destination&&destination.expiresAt>now){
    for(const [key,{result,origin,hotel}] of Object.entries(transport)){
      const observedAt=Date.parse(result.queried_at);
      const expiresAt=Math.min(destination.expiresAt,observedAt+120000);
      const walk=result.return_walk;
      if(walk&&observedAt<=now&&expiresAt>now&&result.data_mode===destination.dataMode
        &&result.hotel_match_basis==='exact_fields'&&samePlace(origin,destination)
        &&walk.origin_name===origin.name&&walk.destination_name===hotel.name){
        cardWalks[key]={metres:walk.distance_meters,expiresAt};
      }
    }
  }
  const visibleKeys=new Set<string>();
  for(const source of sources){
    const search=source.search;
    if(search){
      const filter=filters[search.request_id]??emptyHotelLocalFilters;
      const visible=selectLocalHotels(search.hotels,{
        sort:'source',keyword:filter.keyword,maxPriceFen:parseHotelLocalPrice(filter.maxPrice).fen,
      });
      for(const hotel of visible)visibleKeys.add(parallelHotelKey(search.request_id,hotel.id));
    }else if(source.id==='flyai'){
      source.candidates.forEach((_,index)=>visibleKeys.add('flyai:'+index));
    }
  }
  const recommendations=recommendParallel({...batch,sources},quotes,walks,requireWalk,now,cardWalks);
  const shortlist=shortlistParallel(recommendations.filter(row=>visibleKeys.has(row.key)));
  const hasConflict=Object.values(quotes).some(conflictingParallelQuote);
  function onTransport(key:string,observation:TransportObservation){
    if(observation.result.provider==='didi')return;
    setNow(Date.now());
    setTransport(previous=>({...previous,[key]:observation}));
  }
  function onQuote(key:string,result:QuotesResponse|null){
    setNow(Date.now());
    setQuotes(previous=>{
      const next={...previous};
      const retained=rememberParallelQuote(previous[key],result);
      if(retained)next[key]=retained;
      else delete next[key];
      return next;
    });
  }
  async function nextPage(search:SearchResponse){
    if(!search.next_page||paging)return;
    const controller=new AbortController();
    active.current=controller;
    setPaging(true);setPageError('');
    const timeout=window.setTimeout(()=>controller.abort(),65000);
    try{
      const next=await loadTuniuPage(search.request_id,search.next_page,controller.signal);
      if(controller.signal.aborted)throw new Error('途牛后续页查询超时。');
      if(next.source.id!=='tuniu'||next.request_id!==search.request_id||next.current_page!==search.next_page
        ||next.status==='unavailable')throw new Error(next.error?.message??'途牛后续页未完成。');
      setPages(next);
    }catch(error){
      setPages({...search,next_page:null});
      setPageError(error instanceof Error?error.message:'途牛后续页未完成，原有结果已保留。');
    }finally{
      window.clearTimeout(timeout);setPaging(false);
    }
  }
  return <section className="parallel-hotel-results" aria-label="三家住宿来源结果">
    <div className="state-message parallel-recommendations">
      <h3>寻栖自动推荐</h3>
      <p>{shortlist[0]?.tier==='reference'?'以下是通过本轮条件核对的参考候选。':
        shortlist.length?'以下候选值得优先核对。':
        recommendations.length?'当前筛选下没有可推荐的候选。':'本轮没有可推荐的候选。'}</p>
      {hasConflict&&<p>已发现同店或入住条件冲突的候选已从推荐中排除。</p>}
      {shortlist.length>0&&<ol>{shortlist.map(row=><li key={row.key}>
        <a href={'#'+parallelHotelAnchor(batch.operation_id,row.key)}>{row.name}</a> · {row.source}
        <small>{row.reasons.join(' · ')}</small>
      </li>)}</ol>}
    </div>
    {sources.map(source=>{
      const search=source.search;
      const count=search?.hotels.length??source.candidates.length;
      return <section key={source.id} className="parallel-source" aria-label={labels[source.id]+'搜索结果'}>
        <h3>{labels[source.id]} <small>{states[source.status]}{count>0?' · '+count+' 家':''}</small></h3>
        {source.unavailable_reason&&<p>{errors[source.unavailable_reason]??'本次未取得可用结果'}</p>}
        {count===0&&source.status==='complete'&&<p>本轮没有返回候选。</p>}
        {count===0&&(source.status==='partial'||source.status==='limited')&&<p>已查范围内没有可显示候选。</p>}
        {batch.context.poi_name&&source.id==='tuniu'&&<p>按城市查找并附地标提示。返回范围及步行距离待核实。</p>}
        {batch.context.poi_name&&source.id==='flyai'&&<p>按已确认城市查找，距目的地的步行距离待核实。</p>}
        {!!search?.warnings.length&&<details className="search-scope"><summary>查看{labels[source.id]}的报价说明</summary>
          <ul>{search.warnings.map(warning=><li key={warning}>{warning}</li>)}</ul>
        </details>}
        {search?.hotels.length ? <HotelResults key={search.request_id} result={search} multi={null} hotels={search.hotels} onQuote={onQuote}
          parallelOperationId={batch.operation_id} filterView={{value:filters[search.request_id]??emptyHotelLocalFilters,
            onChange:value=>setFilters(previous=>({...previous,[search.request_id]:value}))}}
          destination={destination} onTransport={(key,observation)=>{
            if(observation.result.data_mode===search.data_mode)onTransport(key,observation);
          }}/> : null}
        {source.id==='tuniu'&&search?.next_page&&<button type="button" className="button outline"
          onClick={()=>void nextPage(search)} disabled={paging}>{paging?'正在查询…':'查看途牛下一页（消耗 API 请求）'}</button>}
        {source.id==='tuniu'&&pageError&&<p role="alert">{pageError}</p>}
        {source.id==='flyai'&&count>0&&<>
          <p>飞猪提供搜索线索，入住人数、间数、币种和报价口径待核实。</p>
          <ul>{source.candidates.map((hotel,index)=><li key={index} className="parallel-hotel-target" id={parallelHotelAnchor(batch.operation_id,'flyai:'+index)} tabIndex={-1}>
            <strong>{hotel.name}</strong> · {hotel.address??'地址待核实'}
            {hotel.price_text&&<> · 来源展示 {hotel.price_text}</>}
            <HotelTransport hotel={{id:index+1,name:hotel.name,address:hotel.address}}
              requestId={'flyai:'+batch.operation_id} context={batch.context} destination={destination}
              onObservation={observation=>onTransport('flyai:'+index,observation)}/>
          </li>)}</ul>
        </>}
      </section>;
    })}
  </section>;
}
