import {useEffect, useId, useRef, useState} from 'react';
import {emptyHotelLocalFilters,parseHotelLocalPrice,selectLocalHotels} from './hotelLocalView';
import type {HotelLocalFilters,HotelLocalSort} from './hotelLocalView';
import ComparisonDock from './ComparisonDock';
import CandidateComparison from './CandidateComparison';
import HotelCard from './HotelCard';
import useHotelListMotion from './useHotelListMotion';
import type {Hotel,QuotesResponse,RecallResponse,SearchResponse} from './types';
import {parallelHotelAnchor,parallelHotelKey} from './parallelRecommendations';
import type {ConfirmedDestination} from './HotelTransport';
import type {TransportObservation} from './TravelPanel';

export default function HotelResults({result,multi,hotels,offline=false,onQuote,destination,onTransport,filterView,parallelOperationId}:{
  result:SearchResponse|RecallResponse;
  multi:RecallResponse|null;
  hotels:Hotel[];
  offline?:boolean;
  onQuote?:(key:string,result:QuotesResponse|null)=>void;
  destination?:ConfirmedDestination|null;
  onTransport?:(key:string,observation:TransportObservation)=>void;
  filterView?:{value:HotelLocalFilters;onChange:(value:HotelLocalFilters)=>void};
  parallelOperationId?:string;
}) {
  const fieldId=useId();
  const [selectedIds,setSelectedIds]=useState<number[]>([]);
  const [sort,setSort]=useState<HotelLocalSort>('source');
  const [localFilters,setLocalFilters]=useState(emptyHotelLocalFilters);
  const {keyword,maxPrice}=filterView?.value??localFilters;
  const setFilters=filterView?.onChange??setLocalFilters;
  // A different supplier request is not the same set of candidates.
  // Revisions of one request (paging/continued recall) intentionally preserve local choices.
  useEffect(()=>{setSelectedIds([]);setSort('source');setLocalFilters(emptyHotelLocalFilters);},[result.request_id]);
  const rootRef=useRef<HTMLElement>(null);
  const gridRef=useRef<HTMLDivElement>(null);
  const priceLimit=parseHotelLocalPrice(maxPrice);
  const displayedHotels=selectLocalHotels(hotels,{sort,keyword,maxPriceFen:priceLimit.fen});
  const filtersActive=sort!=='source'||keyword.trim()!==''||maxPrice.trim()!=='';
  const resetLocalView=()=>{setSort('source');setFilters(emptyHotelLocalFilters);};
  useHotelListMotion(gridRef,result.request_id+':'+displayedHotels.map(hotel=>hotel.id).join(','));
  const selected=selectedIds.map(id=>hotels.find(hotel=>hotel.id===id))
    .filter((hotel):hotel is Hotel=>hotel!==undefined);
  // In a continued recall, the round timestamp is not proof of an individual hotel's observation.
  const observedAt=(id:number)=>multi
    ? (multi.hotel_observations.find(item=>item.hotel_id===id)?.queried_at ?? '')
    : result.queried_at;
  const viewComparison=()=>{
    const target=rootRef.current?.querySelector<HTMLElement>('.candidate-comparison');
    if (!target || typeof target.scrollIntoView!=='function')return;
    const reduce=typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({behavior:reduce?'auto':'smooth',block:'start'});
    target.focus({preventScroll:true});
  };
  const toggle=(id:number)=>setSelectedIds(previous=>{
    if(previous.includes(id))return previous.filter(value=>value!==id);
    if(previous.length>=3||!hotels.some(hotel=>hotel.id===id))return previous;
    return [...previous,id];
  });

  return <section className={'candidate-results'+(selected.length?' has-selection':'')} aria-label="同轮酒店参考候选" ref={rootRef}>
    <div className="candidate-selection">
      <div>
        <h3>对比你喜欢的酒店</h3>
        <p>选中酒店卡片上的“加入对比”，最多比较3家。{offline?'这里展示的是演示价格，不是实时房价。':'仅比较当前参考价格与位置，不会自动刷新价格。'}</p>
      </div>
      <p role="status">已选 {selected.length}/3 家{selected.length===1?' · 再选一家即可对比':selected.length===3?' · 已达上限':''}</p>
    </div>
    <CandidateComparison
      items={selected.map(hotel=>({hotel,observedAt:observedAt(hotel.id)}))}
      onClear={()=>setSelectedIds([])}
      onRemove={id=>toggle(id)}
      isTest={result.data_mode==='test'}
      offline={offline}
    />
    <section className="hotel-local-tools" aria-label="本地整理已返回酒店">
      <div className="hotel-local-heading">
        <div><strong>整理已找到的酒店</strong><p>只整理这次已经返回的候选，不会重新查价。</p></div>
        <span role="status" className="hotel-local-count">显示 {displayedHotels.length} / {hotels.length} 家</span>
      </div>
      <div className="hotel-local-fields">
        <label htmlFor={fieldId+'-keyword'}>筛选名称或地址
          <input id={fieldId+'-keyword'} type="search" value={keyword} onChange={event=>setFilters({keyword:event.target.value,maxPrice})} placeholder="酒店名称、街道或区域" maxLength={100}/>
        </label>
        <label htmlFor={fieldId+'-sort'}>排序方式
          <select id={fieldId+'-sort'} value={sort} onChange={event=>setSort(event.target.value as HotelLocalSort)}>
            <option value="source">来源原顺序</option>
            <option value="price_low">参考价从低到高</option>
            <option value="price_high">参考价从高到低</option>
          </select>
        </label>
        <label htmlFor={fieldId+'-price'}>人民币参考价上限（元）
          <input id={fieldId+'-price'} type="text" inputMode="decimal" value={maxPrice} onChange={event=>setFilters({keyword,maxPrice:event.target.value})} placeholder="不限金额" maxLength={10} aria-invalid={priceLimit.invalid} aria-describedby={fieldId+'-hint'}/>
        </label>
        <button type="button" className="hotel-local-reset" onClick={resetLocalView} disabled={!filtersActive}>清除本地筛选</button>
      </div>
      {priceLimit.invalid&&<p className="hotel-local-error" role="alert">金额格式有误，已暂不执行价格上限筛选。请输入非负金额，最多两位小数。</p>}
      <p id={fieldId+'-hint'} className="hotel-local-hint">仅依据已返回的人民币搜索参考价；非最终含费价或可订证明。设定金额上限时，缺价或非人民币候选不显示；排序时它们始终排在最后。</p>
    </section>
    {displayedHotels.length===0&&<div className="hotel-local-empty" role="status"><strong>本地筛选没有匹配的酒店</strong><p>这里只是隐藏了当前已返回的候选，不代表来源没有房间。可修改关键词、金额上限，或清除上方筛选恢复展示。</p></div>}
    <div className="hotel-grid" ref={gridRef}>{displayedHotels.map(hotel=><div className={parallelOperationId?'recall-hotel-group parallel-hotel-target':'recall-hotel-group'}
      id={parallelOperationId?parallelHotelAnchor(parallelOperationId,parallelHotelKey(result.request_id,hotel.id)):undefined}
      tabIndex={parallelOperationId?-1:undefined} key={result.request_id+'-'+hotel.id}>
      {multi&&<p className="branch-membership">关联检索门店：{multi.branches.filter(item=>item.hotel_ids.includes(hotel.id)).map(item=>item.name).join('、')}</p>}
      <HotelCard
        hotel={hotel}
        requestId={result.request_id}
        queriedAt={observedAt(hotel.id)}
        context={result.context}
        sourceName={result.source.name}
        sourceId={onQuote?result.source.id:undefined}
        onQuote={onQuote?quote=>onQuote(parallelHotelKey(result.request_id,hotel.id),quote):undefined}
        transport={onTransport?{
          destination,
          onObservation:observation=>onTransport(parallelHotelKey(result.request_id,hotel.id),observation),
        }:undefined}
        offline={offline}
        compareSelected={selectedIds.includes(hotel.id)}
        compareDisabled={selectedIds.length>=3&&!selectedIds.includes(hotel.id)}
        onCompareToggle={()=>toggle(hotel.id)}
      />
    </div>)}</div>
    {selected.length>0&&<ComparisonDock items={selected} onClear={()=>setSelectedIds([])} onRemove={id=>toggle(id)} onView={viewComparison}/>}
  </section>;
}
