import {useEffect,useId,useRef,useState} from 'react';
import {travelPlaces,travelRoute} from './api';
import {PlanAssessmentCard} from './DecisionPanel';
import {money,timeText} from './format';
import type {Hotel,PlaceOption,SearchInput,TravelPlacesResponse,TravelRouteResponse} from './types';

type Provider='amap'|'tencent'|'didi';
const names:Record<Provider,string>={amap:'高德地图',tencent:'腾讯地图',didi:'滴滴估价'};
const checkText=(value:boolean|null,label:string)=>value===true?label+'一致':value===false?label+'不同':label+'未核实';

export type TransportObservation = {
  result:TravelRouteResponse;
  origin:PlaceOption;
  hotel:PlaceOption;
};

export default function TravelPanel({hotel,requestId,context,offline=false,onResult}:{
  hotel:Pick<Hotel,'id'|'name'|'address'>;requestId:string;context:SearchInput;offline?:boolean;
  onResult?:(observation:TransportObservation|null)=>void;
}) {
  const destinationMode=!!onResult&&!offline;
  const [provider,setProvider]=useState<Provider>('amap');
  const [city,setCity]=useState(context.place_type==='城市'?context.place:'');
  const [keyword,setKeyword]=useState(destinationMode
    ? context.poi_name??(context.place_type==='城市'?'':context.place)
    : '海底捞');
  const [places,setPlaces]=useState<TravelPlacesResponse|null>(null);
  const [result,setResult]=useState<TravelRouteResponse|null>(null);
  const [hotelId,setHotelId]=useState('');
  const [restaurantId,setRestaurantId]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  const [loading,setLoading]=useState<'places'|'route'|null>(null);
  const [error,setError]=useState('');
  const [,tick]=useState(0);
  const active=useRef<AbortController|null>(null);
  const sequence=useRef(0);
  useEffect(()=>{
    const interval=window.setInterval(()=>tick(n=>n+1),30000);
    return ()=>{sequence.current++;active.current?.abort();window.clearInterval(interval);};
  },[]);
  function clearTravelResult() {
    setResult(null);
    onResult?.(null);
  }
  function clear() {
    sequence.current++;active.current?.abort();setLoading(null);setPlaces(null);clearTravelResult();
    setHotelId('');setRestaurantId('');setConfirmed(false);setError('');
  }
  async function resolve() {
    if(!city.trim()||!keyword.trim()){
      setError(destinationMode?'请填写城市和具体目的地。':'请填写城市和餐厅名称。');
      return;
    }
    active.current?.abort();const controller=new AbortController();active.current=controller;
    const current=++sequence.current;setLoading('places');setError('');setPlaces(null);clearTravelResult();
    setConfirmed(false);setHotelId('');setRestaurantId('');
    const timeout=window.setTimeout(()=>controller.abort(),65000);
    try{
      const origin=keyword.trim();
      const data=await travelPlaces(hotel.id,{
        request_id:requestId,provider,city:city.trim(),
        restaurant_keyword:destinationMode?'海底捞':origin,
        destination_keyword:destinationMode?origin:null,
      },controller.signal);
      if(current!==sequence.current||controller.signal.aborted)return;
      if(data.hotel_id!==hotel.id||data.request_id!==requestId||data.provider!==provider)throw new Error('地点查询条件已变化，请重新检索。');
      if(data.status==='unavailable')throw new Error(data.error?.message||'暂未取得地点信息。');
      setPlaces(data);setHotelId(data.matching.status==='matched'?data.matching.selected_id??'':'');
      setRestaurantId(!destinationMode&&data.restaurants.length===1?data.restaurants[0].id:'');
    }catch(reason){
      if(current!==sequence.current)return;
      setError(reason instanceof DOMException&&reason.name==='AbortError'?'查询超时，请重新检索。':reason instanceof Error?reason.message:'暂未取得地点信息。');
    }finally{window.clearTimeout(timeout);if(current===sequence.current)setLoading(null);}
  }
  async function compute() {
    const origin=places?.restaurants.find(point=>point.id===restaurantId);
    const targetHotel=places?.hotels.find(point=>point.id===hotelId);
    if(!places?.context_id||!origin||!targetHotel)return;
    active.current?.abort();const controller=new AbortController();active.current=controller;
    const current=++sequence.current;setLoading('route');clearTravelResult();setError('');
    const timeout=window.setTimeout(()=>controller.abort(),65000);
    try{
      const data=await travelRoute(hotel.id,{request_id:requestId,context_id:places.context_id,hotel_option_id:hotelId,restaurant_option_id:restaurantId,confirm_hotel:confirmed},controller.signal);
      if(current!==sequence.current||controller.signal.aborted)return;
      if(data.request_id!==requestId||data.hotel_id!==hotel.id||data.provider!==provider)throw new Error('路线查询条件已变化，请重新检索。');
      if(data.status==='unavailable')throw new Error(data.error?.message||'暂未取得出行参考。');
      if(destinationMode&&(data.restaurant_name!==origin.name||data.hotel_name!==targetHotel.name)){
        throw new Error('来源返回的路线地点与所选地点不一致。');
      }
      setResult(data);
      onResult?.({result:data,origin,hotel:targetHotel});
    }catch(reason){
      if(current!==sequence.current)return;
      setError(reason instanceof DOMException&&reason.name==='AbortError'?'查询超时，请重试。':reason instanceof Error?reason.message:'暂未取得出行参考。');
    }finally{window.clearTimeout(timeout);if(current===sequence.current)setLoading(null);}
  }
  const selected=places?.hotels.find(p=>p.id===hotelId);
  const exact=places?.matching.status==='matched'&&places.matching.selected_id===hotelId;
  const expired=places!==null&&Date.now()-Date.parse(places.queried_at)>places.expires_in_seconds*1000;
  const oldFare=result?.provider==='didi'&&Date.now()-Date.parse(result.queried_at)>120000;
  const labelId=useId();
  const originLabel=destinationMode?'目的地':'餐厅';
  const routeLabel=destinationMode?'查询路线':'查询交通与地铁';
  return <section className="travel-panel" aria-label={hotel.name+'的位置与返程'}>
    <div className="travel-heading"><div><h4>位置与返程参考</h4><p>{offline?'读取固定模拟地点与路线，核对规则口径；不连接地图或打车接口。':'先确认对应地点，再核对实际路线或当前车费。'}</p></div><span className="source-label">{offline?'广州测试夹具':'按需查询'}</span></div>
    <div className="travel-tabs" role="group" aria-label="出行来源">
      {(Object.keys(names) as Provider[]).map(p=><button key={p} type="button" className={provider===p?'selected':''} onClick={()=>{if(p!==provider){clear();setProvider(p);}}} aria-pressed={provider===p}>{names[p]}</button>)}
    </div>
    <div className="travel-query">
      <div><label htmlFor={labelId+'-city'}>所在城市</label><input id={labelId+'-city'} value={city} maxLength={40} onChange={e=>{clear();setCity(e.target.value);}} placeholder="例如：广州"/></div>
      <div><label htmlFor={labelId+'-restaurant'}>{destinationMode?'目的地名称或地址':'餐厅品牌或门店'}</label><input id={labelId+'-restaurant'} value={keyword} maxLength={destinationMode?120:30} onChange={e=>{clear();setKeyword(e.target.value);}} placeholder={destinationMode?'例如：永庆坊':'例如：海底捞'}/></div>
      <button type="button" className="button outline" onClick={()=>void resolve()} disabled={loading!==null}>{loading==='places'?'检索中…':'检索地点'}</button>
    </div>
    <p className="travel-identity">对应住宿：{hotel.name}<span>{hotel.address||'原住宿地址尚未取得'}</span></p>
    {error&&<div className="travel-error" role="alert">{error}</div>}
    {places&&<>
      {(offline||places.data_mode==='test')&&<p className="test-banner">广州测试夹具 · 非实时地图回显</p>}
      <div className={'match-notice '+(places.matching.status==='matched'?'matched':'uncertain')}><strong>{places.matching.status==='matched'?(places.matching.basis==='geocode'?'同店匹配通过（地理编码核对）':places.matching.basis==='address_core'?'同店匹配通过（地址主干一致）':'严格同店匹配通过'):places.matching.status==='ambiguous'?'同名地点存在歧义':'同店匹配尚未确认'}</strong><p>{places.matching.explanation}</p></div>
      <div className="place-selectors">
        <div><label htmlFor={labelId+'-hotel'}>住宿地图地点</label><select id={labelId+'-hotel'} value={hotelId} disabled={loading==='route'} onChange={e=>{setHotelId(e.target.value);setConfirmed(false);clearTravelResult();setError('');}}><option value="">请选择并核对住宿</option>{places.hotels.map(p=><option key={p.id} value={p.id}>{p.name}{p.address?' · '+p.address:''}</option>)}</select>
          {selected&&<div className="selected-place"><strong>{selected.name}</strong><p>{selected.address||'地址未取得'} · {selected.city||'城市未取得'}</p><small>{checkText(selected.name_match,'名称')} · {checkText(selected.address_match,'地址')} · {checkText(selected.city_match,'城市')}</small></div>}
        </div>
        <div><label htmlFor={labelId+'-branch'}>{destinationMode?'确认具体目的地':'具体餐厅门店'}</label><select id={labelId+'-branch'} value={restaurantId} disabled={loading==='route'} onChange={e=>{setRestaurantId(e.target.value);clearTravelResult();setError('');}}><option value="">{destinationMode?'请选择具体地点':'请选择具体门店'}</option>{places.restaurants.map(p=><option key={p.id} value={p.id}>{p.name}{p.address?' · '+p.address:''}</option>)}</select>
          {restaurantId&&<p className="branch-address">{places.restaurants.find(p=>p.id===restaurantId)?.address||'该门店地址尚未取得'}</p>}
        </div>
      </div>
      {hotelId&&!exact&&<label className="place-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已核对名称和地址，确认所选地点对应这家住宿</label>}
      <p className="coverage-note">{places.coverage_note} · {offline?'数据集读取于':'查询于'} {timeText(places.queried_at)}</p>
      {places.warnings.length>0&&<div className="travel-warnings">{places.warnings.map(w=><p key={w}>{w}</p>)}</div>}
      {expired&&<p className="stale-note">地点选择已超过有效时间，请重新检索地点。</p>}
      {places.hotels.length===0||places.restaurants.length===0?<p className="travel-empty">本次范围内缺少可选地点，请调整城市或{originLabel}关键词。</p>:
        <button type="button" className="button primary travel-compute" disabled={loading!==null||!hotelId||!restaurantId||(!exact&&!confirmed)||expired} onClick={()=>void compute()}>{loading==='route'?'正在核对…':result?'刷新当前参考':provider==='didi'?'查询当前车费':routeLabel}</button>}
    </>}
    {result&&<div className="travel-result">
      {(offline||result.data_mode==='test')&&<p className="test-banner">广州测试夹具 · 非实时路线或估价</p>}
      <div className="travel-result-top"><strong>{offline?'模拟地点与路线':names[result.provider]} · {(offline||result.data_mode==='test')?(result.provider==='didi'?'模拟当前车费参考':'模拟步行路线'):(result.provider==='didi'?'当前车费估价':'交通路线参考')}</strong><small>{offline?'数据集读取于':'查询于'} {timeText(result.queried_at)}</small></div>
      <p className="route-direction">{result.restaurant_name}<span aria-hidden="true"> → </span>{result.hotel_name}</p>
      <p className="identity-basis">{result.hotel_match_basis==='exact_fields'?'住宿按名称、地址、城市唯一匹配':'住宿对应关系已由你核对确认'}</p>
      {result.return_walk&&<div className="walk-reference"><span>{destinationMode?'目的地到住宿':'餐厅返回住宿'}（步行）</span><strong>{Math.ceil(result.return_walk.duration_seconds/60)}<small> 分钟</small></strong><p>{result.return_walk.distance_meters}米 · 来源预计{result.return_walk.duration_seconds}秒</p><small>按地图地点计算，实际入口及夜间通道未核实</small></div>}
      {result.return_drive&&<div className="walk-reference drive-reference"><span>{destinationMode?'目的地到住宿':'餐厅返回住宿'}（驾车参考）</span><strong>{Math.ceil(result.return_drive.duration_seconds/60)}<small> 分钟</small></strong><p>{result.return_drive.distance_meters}米 · 来源预计{result.return_drive.duration_seconds}秒</p><small>不含司机接驾/候车，不保证未来路况</small></div>}
      {result.provider!=='didi'&&<p className="transport-status">{result.transport_status==='pass_walk_or_drive'?'交通子指标：步行≤1500米或驾车≤900秒，满足其中一项（不含接驾等待与未来路况）。':result.transport_status==='rejected_both_modes_over_limit'?'交通子指标：步行距离与驾车预计均超过时限，不满足。':'交通子指标：步行或驾车证据不完整，暂不能判断是否满足。'}</p>}
      {result.subway&&<div className="subway-reference"><strong>住宿到地铁</strong><span>{result.subway.name} · {result.subway.precision==='named_entrance_poi'?'地图标注入口':'站点，入口未核实'}</span><p>步行预计{Math.ceil(result.subway.walk.duration_seconds/60)}分钟 · {result.subway.walk.distance_meters}米</p></div>}
      {result.fares.length>0&&<div className="fare-grid">{result.fares.map((fare,index)=><div className={fare.name==='快车'?'fare-item ordinary':'fare-item'} key={index}><strong>{fare.name}</strong><span>{fare.upper_fen===null?'金额待核实':fare.lower_fen!==fare.upper_fen?'¥'+money(fare.lower_fen)+'—'+money(fare.upper_fen):'¥'+money(fare.upper_fen)}</span><small>当前预估 · 容量及优惠适用待核实</small></div>)}</div>}
      {oldFare&&<p className="stale-note">车费查询已超过2分钟，请重新检索地点并刷新；来源未提供明确报价有效期。</p>}
      {(result.opening_today||result.opening_week)&&<p className="opening-reference">来源营业信息：{[result.opening_today,result.opening_week].filter(Boolean).join('；')}。不保证未来到访时段营业。</p>}
      <div className="travel-warnings">{result.warnings.map(warning=><p key={warning}>{warning}</p>)}</div>
      {result.return_trip&&<div className="route-decision"><h5>返程判定（仅使用本次路线响应内的证据）</h5><PlanAssessmentCard data={result.return_trip}/></div>}
    </div>}
    <p className="travel-scope-note">位置与车费只作补充参考。住宿费用、夜间可达和载客条件仍需核实；综合成本只在本次响应证据范围内给出已知费用小计，不跨响应拼接，也不代表可订。</p>
  </section>;
}
