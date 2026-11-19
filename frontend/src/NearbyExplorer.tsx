import HotelResults from './HotelResults';
import NearbyIdentityPanel from './NearbyIdentityPanel';
import {useEffect, useMemo, useRef, useState} from 'react';
import {exploreNearby,exploreParallelNearby,resolveNearby,walkNearby,searchHotels,parallelSearchHotels,cancelParallelSearch} from './api';
import ParallelHotelResults from './ParallelHotelResults';
import {parallelHotelKey} from './parallelRecommendations';
import type {WalkEvidence} from './parallelRecommendations';
import {comparableNearbyCount,mixedNearbyReviewSources,nearbyGuestReview,nearbyPoiRating,nearbyWalkMetres,rankNearbyHotels} from './nearbyRanking';
import type {NearbySort} from './nearbyRanking';
import type {
  Hotel,NearbyExploreResponse,NearbyHotelOption,
  NearbyRecommendationLink,NearbyResolveResponse,NearbyWalkResponse,ParallelSearchResponse,SearchInput,SearchResponse,
} from './types';

type Props={
  requestId:string|null;
  context:SearchInput;
  hotels:Hotel[];
  offline:boolean;
  parallel?:boolean;
};
const textError=(reason:unknown)=>reason instanceof Error?reason.message:'操作没有完成，请确认地点后重试。';
const metres=(distance:number)=>distance>=1000?(distance/1000).toFixed(2)+' 公里':distance+' 米';

export default function NearbyExplorer({requestId,context,hotels,offline,parallel=false}:Props){
  const [name,setName]=useState(context.place_type==='城市'?'':context.place);
  const [city,setCity]=useState(context.place_type==='城市'?context.place:'');
  const [provider,setProvider]=useState<'amap'|'tencent'>('amap');
  const [resolvedCity,setResolvedCity]=useState('');
  const [resolved,setResolved]=useState<NearbyResolveResponse|null>(null);
  const [expiresAt,setExpiresAt]=useState(0);
  const [selected,setSelected]=useState('');
  const [sourceSearch,setSourceSearch]=useState<SearchResponse|null>(null);
  const [batch,setBatch]=useState<ParallelSearchResponse|null>(null);
  const [sourceAttempted,setSourceAttempted]=useState(false);
  const [nearby,setNearby]=useState<NearbyExploreResponse|null>(null);
  const [recommendationLinks,setRecommendationLinks]=useState<NearbyRecommendationLink[]>([]);
  const [walks,setWalks]=useState<Record<string,NearbyWalkResponse>>({});
  const [walkAttempts,setWalkAttempts]=useState<string[]>([]);
  const [busy,setBusy]=useState<'resolve'|'nearby'|string|null>(null);
  const [error,setError]=useState('');
  const [maxMetres,setMaxMetres]=useState(2000);
  const [tick,setTick]=useState(0);
  useEffect(()=>{
    if(!nearby)return;
    const timer=window.setInterval(()=>setTick(value=>value+1),1000);
    return ()=>window.clearInterval(timer);
  },[nearby]);
  const [sort,setSort]=useState<NearbySort>('distance');
  const [verifiedOnly,setVerifiedOnly]=useState(false);
  const active=useRef<AbortController|null>(null);
  const generation=useRef(0);
  const rankingContext=Date.now()<expiresAt?(nearby?.context_id??''):'';
  useEffect(()=>()=>{
    generation.current++;
    active.current?.abort();
  },[]);
  const reset=()=>{
    generation.current++;
    active.current?.abort();
    setBusy(null);setError('');setResolved(null);setSelected('');setExpiresAt(0);
    setNearby(null);setRecommendationLinks([]);setWalks({});setWalkAttempts([]);setSourceSearch(null);setBatch(null);setSourceAttempted(false);
    setMaxMetres(2000);setSort('distance');setVerifiedOnly(false);
  };
  const found=useMemo(()=>nearby?.hotels.filter(item=>
    item.straight_line_meters<=maxMetres &&
    (!verifiedOnly || (nearbyWalkMetres(item,walks,rankingContext,nearby.provider)??Infinity)<=1500)
  )??[],[nearby,maxMetres,verifiedOnly,walks,rankingContext,tick]);
  const linked=useMemo(()=>new Map((sourceAttempted?(sourceSearch?.hotels??[]):hotels).map(hotel=>[hotel.id,hotel])),[hotels,sourceSearch,sourceAttempted]);
  const qualifying=found.filter(item=>item.source_match==='matched'&&item.source_hotel_id!==null&&linked.has(item.source_hotel_id));
  const sourceCount=qualifying.length;
  const hasGuestReviews=found.some(point=>nearbyGuestReview(point)!==null);
  const activeSort=!hasGuestReviews&&(sort==='rating'||sort==='balanced')?'distance':sort;
  const displayed=useMemo(
    ()=>rankNearbyHotels(found,linked,activeSort,nearby?.provider==='tencent'?1000:2000,walks,
      rankingContext,nearby?.provider??''),
    [found,linked,activeSort,walks,nearby?.provider,rankingContext,tick],
  );
  const comparableCount=comparableNearbyCount(found,linked,walks,rankingContext,nearby?.provider??'');
  const mixedSources=mixedNearbyReviewSources(found,linked,walks,rankingContext,nearby?.provider??'');
  const recommendationWalks:Record<string,WalkEvidence>={};
  if(nearby&&rankingContext){
    for(const link of recommendationLinks){
      const point=nearby.hotels.find(hotel=>hotel.id===link.hotel_option_id);
      if(!point)continue;
      const metres=nearbyWalkMetres(point,walks,rankingContext,nearby.provider);
      if(metres!==null)recommendationWalks[parallelHotelKey(link.request_id,link.hotel_id)]={metres,expiresAt};
    }
  }

  async function resolve(){
    const keyword=name.trim(),placeCity=city.trim();
    if(!keyword||!placeCity){setError('请先填写具体地点名称和所属国内城市，不能只靠空白城市猜测。');return;}
    if(offline)return;
    reset();
    const version=++generation.current,controller=new AbortController();active.current=controller;
    setBusy('resolve');
    const startedAt=Date.now();
    try {
      const response=await resolveNearby({provider,keyword,city:placeCity,country_region:'中国大陆'},controller.signal);
      if(version!==generation.current||controller.signal.aborted)return;
      if(response.status==='unavailable'){setError(response.error?.message??'地图地点检索暂未完成。');return;}
      setResolved(response);setResolvedCity(placeCity);setExpiresAt(startedAt+response.expires_in_seconds*1000);setError('');
    } catch(reason) {
      if(version===generation.current&&!controller.signal.aborted)setError(textError(reason));
    } finally {
      if(version===generation.current)setBusy(null);
    }
  }
  async function findSourceHotels(){
    const point=resolved?.options.find(item=>item.id===selected);
    if(!point||offline||busy||sourceAttempted)return;
    if(Date.now()>=expiresAt){setError('地点已过期，请重新搜索并确认。');return;}
    const version=++generation.current,controller=new AbortController();active.current=controller;
    setSourceAttempted(true);setBusy('source');setError('');setSourceSearch(null);setBatch(null);
    const timer=window.setTimeout(()=>controller.abort(),65000);
    const operation=parallel?crypto.randomUUID():null;
    const cancel=()=>{if(operation)void cancelParallelSearch(operation).catch(()=>{});};
    controller.signal.addEventListener('abort',cancel,{once:true});
    try{
      const input:SearchInput={...context,place:resolvedCity,place_type:'城市',
        country_region:'中国大陆',poi_name:point.name,limit:Math.min(context.limit,10)};
      if(operation){
        const response=await parallelSearchHotels(input,operation,controller.signal);
        if(version!==generation.current||controller.signal.aborted)return;
        if(response.operation_id!==operation||response.status==='cancelled')throw new Error('本次查询已取消或条件已变化。');
        const preferred=response.sources.find(source=>source.id==='rollinggo'&&source.search?.hotels.length)?.search
          ??response.sources.find(source=>source.search?.hotels.length)?.search;
        setSourceSearch(preferred??null);setBatch(response);
        return;
      }
      const response=await searchHotels(input,controller.signal);
      if(version!==generation.current||controller.signal.aborted)return;
      if(response.status==='unavailable')throw new Error(response.error?.message??'目的地住宿来源查询未完成。');
      setSourceSearch(response);
    }catch(reason){
      if(version===generation.current)setError(controller.signal.aborted?'住宿来源查询已超时，未自动重试。':textError(reason));
    }finally{
      controller.signal.removeEventListener('abort',cancel);
      window.clearTimeout(timer);
      if(version===generation.current)setBusy(null);
    }
  }
  async function findNearby(){
    if(!resolved?.context_id||!selected||offline)return;
    const version=++generation.current,controller=new AbortController();active.current=controller;
    setBusy('nearby');setError('');setNearby(null);setRecommendationLinks([]);setWalks({});setWalkAttempts([]);
    try {
      const input={
        context_id:resolved.context_id,anchor_id:selected,request_id:sourceAttempted?(sourceSearch?.request_id??null):requestId,
      };
      const response=batch?await exploreParallelNearby({...input,operation_id:batch.operation_id},controller.signal)
        :await exploreNearby(input,controller.signal);
      if(version!==generation.current||controller.signal.aborted)return;
      if(response.status==='unavailable'){setError(response.error?.message??'周边酒店地点未能核实。');return;}
      if('recommendation_links' in response)setRecommendationLinks(response.recommendation_links as NearbyRecommendationLink[]);
      setNearby(response);
    } catch(reason){
      if(version===generation.current&&!controller.signal.aborted)setError(textError(reason));
    } finally {
      if(version===generation.current)setBusy(null);
    }
  }
  async function verifyWalk(hotel:NearbyHotelOption){
    if(!resolved?.context_id||!nearby||walkAttempts.includes(hotel.id)||walkAttempts.length>=3||offline)return;
    const version=++generation.current,controller=new AbortController();active.current=controller;
    setWalkAttempts(old=>[...old,hotel.id]);setBusy(hotel.id);setError('');
    try {
      const response=await walkNearby({
        context_id:resolved.context_id,hotel_option_id:hotel.id,
      },controller.signal);
      if(version!==generation.current||controller.signal.aborted)return;
      setWalks(old=>({...old,[hotel.id]:response}));
    } catch(reason) {
      if(version===generation.current&&!controller.signal.aborted)setError(textError(reason));
    } finally {
      if(version===generation.current)setBusy(null);
    }
  }
  function supplierPrice(hotel:Hotel):string|null{
    const price=hotel.reference_price_fen;
    if(hotel.currency!=='CNY'||price===null||!Number.isSafeInteger(price)||price<0)return null;
    return (price/100).toLocaleString('zh-CN',{maximumFractionDigits:2,minimumFractionDigits:0});
  }

  return <section className="nearby-explorer" aria-label="目的地周边酒店地图核对">
    <div className="nearby-head">
      <div>
        <span className="nearby-kicker">目的地 · 位置核验</span>
        <h3>这次，真正住在目的地附近</h3>
        <p>先选准地点，再看同一地图的周边酒店。位置、路线和房价分别核实，不会擅自把同名店合并。</p>
      </div>
      <span className="nearby-provenance">地图查找 · 手动触发</span>
    </div>
    {offline?<p className="nearby-neutral">当前是离线夹具，不能验证真实地点和路线；这里不会向地图来源发送请求。</p>:<>
      <div className="nearby-fields">
        <label htmlFor="nearby-place">具体目的地
          <input id="nearby-place" value={name} disabled={!!busy||!!resolved} placeholder="例如：上海迪士尼乐园、海底捞人民广场店" maxLength={120}
            onChange={event=>{setName(event.target.value);reset();}}/>
        </label>
        <label htmlFor="nearby-city">所在城市
          <input id="nearby-city" value={city} disabled={!!busy||!!resolved} placeholder="例如：上海" maxLength={40}
            onChange={event=>{setCity(event.target.value);reset();}}/>
        </label>
        <div className="mainland-scope"><span className="scope-title">地图覆盖范围</span>
          <strong>中国大陆</strong><small>高德地图 / 腾讯地图</small></div>
        <label htmlFor="nearby-map">地图来源
          <select id="nearby-map" disabled={!!busy||!!resolved} value={provider} onChange={event=>{const next=event.target.value as 'amap'|'tencent';setProvider(next);reset();setMaxMetres(next==='tencent'?1000:2000);}}>
            <option value="amap">高德地图</option><option value="tencent">腾讯地图</option>
          </select>
        </label>
      </div>
      <div className="nearby-actions">
        <button className="nearby-primary" type="button" onClick={()=>void resolve()} disabled={!!busy||!!nearby}>
          {busy==='resolve'?'正在识别地点…':'1 · 搜索具体地点（1 次地图请求）'}
        </button>
        {(resolved||nearby)&&<button className="nearby-reset" type="button" disabled={!!busy} onClick={reset}>重新选地点</button>}
        <small>仅使用已接入的中国大陆地图来源；不自动查询、不自动选中地点，也不自动关联房价。</small>
      </div>
      {error&&<p className="nearby-error" role="alert">{error}</p>}
      {resolved&&<div className="nearby-choices">
        <h4>2 · 确认一个地点 <small>已排除明确跨城结果，优先同城与名称相近候选 · 有效约 2 分钟</small></h4>
        <div className="nearby-option-list" role="radiogroup" aria-label="地图候选地点">
          {resolved.options.map(point=><label className={'nearby-option'+(selected===point.id?' selected':'')} key={point.id}>
            <input type="radio" name="nearby-anchor" checked={selected===point.id} disabled={!!busy||!!nearby||sourceAttempted}
              onChange={()=>{setSelected(point.id);setNearby(null);setWalks({});setWalkAttempts([]);setError('');}}/>
            <span><strong>{point.name}</strong><small>{[point.city,point.address].filter(Boolean).join(' · ')||'地址尚未返回'} · {point.category||'类别未核实'}</small></span>
          </label>)}
          {!resolved.options.length&&<p>地图未提供可核对的具体地点。换更精确的名称再搜索，不自动选第一项。</p>}
        </div>
        {!nearby&&<div>
          <button className="nearby-primary" type="button" disabled={!selected||!!busy||sourceAttempted} onClick={()=>void findSourceHotels()}>
            {busy==='source'?'正在查询目的地住宿…':sourceAttempted?'本轮住宿来源已尝试':parallel?'按此目的地并行查三家住宿来源':'按此目的地查住宿来源（一次查询）'}
          </button>
          <p>先向住宿来源查目的地周边，再点击下方地图核对。地点与半径是检索提示，可能被来源忽略；不会自动重试或改查全城。</p>
        </div>}
        {sourceSearch&&<p role="status">已取得目的地住宿参考 {sourceSearch.hotels.length} 家；尚未核实与地图同店，继续核对下方地图。</p>}
        {!nearby&&<button className="nearby-primary" type="button" disabled={!selected||!!busy} onClick={()=>void findNearby()}>
          {busy==='nearby'?'正在找附近酒店…':'3 · 查找此地点附近的酒店（1 次地图请求）'}
        </button>}
      </div>}
      {batch&&<ParallelHotelResults key={batch.operation_id} batch={batch} requireWalk walks={recommendationWalks}/>}
      {sourceSearch&&!batch&&<details><summary>查看本轮目的地住宿来源参考与房型入口</summary>
        <HotelResults result={sourceSearch} multi={null} hotels={sourceSearch.hotels}/>
      </details>}
      {nearby&&<div className="nearby-hotel-section" aria-label="地图周边住宿候选">
        <div className="nearby-hotel-heading">
          <div><h4>周边酒店位置证据</h4><p>地图返回 {nearby.hotels.length} 家，当前显示 {found.length} 家；{sourceCount} 家与本次酒店来源唯一同店匹配。</p><p>{nearby.coverage_note}</p></div>
          <label htmlFor="nearby-radius">直线距离范围
            <select id="nearby-radius" value={maxMetres} onChange={event=>setMaxMetres(Number(event.target.value))}>
              <option value={500}>500 米以内</option><option value={1000}>1 公里以内</option>
              {nearby.provider==='amap'&&<option value={2000}>2 公里以内</option>}
            </select>
          </label>
          <label htmlFor="nearby-sort">参考排序方式
            <select id="nearby-sort" value={activeSort} onChange={event=>setSort(event.target.value as NearbySort)}>
              <option value="distance">交通距离 · 核实步行优先</option>
              <option value="price">参考价 · 同店低价优先</option>
              {hasGuestReviews&&<option value="rating">住客评分优先 · 许可登记</option>}
              {hasGuestReviews&&<option value="balanced">四因素综合参考 · 证据齐全</option>}
            </select>
          </label>
          <label className="nearby-verified-filter"><input type="checkbox" checked={verifiedOnly} onChange={event=>setVerifiedOnly(event.target.checked)}/>
            只看已核实步行 ≤1500 米的酒店</label>
        </div>
        <div className="nearby-ranking-note">
          {!rankingContext&&<p>本次地点证据已过期，请重新选地点；旧路线不参与筛选和综合排序。</p>}
          <strong>{activeSort==='distance'?'距离参考序':activeSort==='price'?'同店参考价排序':activeSort==='rating'?'住客口碑排序':'四因素证据排序'}{hasGuestReviews&&<> · {comparableCount} 家可完整比较</>}</strong>
          <p>{activeSort==='distance'?'已核实步行距离靠前，未核实者才按地图直线距离参考；两者不会混为同一指标。':
            activeSort==='price'?'仅有唯一同店关联、有效人民币参考价的酒店按低价在前；不是最终含费价。':
            activeSort==='rating'?'仅使用本机有授权登记且口径一致的住客评分，地图POI分数不参与口碑排序。':
            mixedSources?'不同点评来源口径不可直接比较，本次不跨平台综合排名。':
            comparableCount<2?'不足两家同时具备同店价格、有许可登记的住客评分、评价数量和本轮步行距离；不宣称已有可信综合排名。':
            '四因素：同店参考价30% + 已核实步行距离25% + 住客评分30% + 评价人数15%。评价量按对数降权、少样本评分向参考值收缩；权重为待样本验证的初始值；缺字段不参与综合分。'}
          {hasGuestReviews&&<> {nearby.ranking_reason}</>} 步行路线最多手动核对 3 次；切换排序不新增地图或酒店请求。</p>
        </div>
        {found.length===0&&<p className="nearby-neutral">{verifiedOnly
          ?'本次没有已经核实步行距离≤1500米的酒店。未核实的酒店不等于超标；请取消该筛选后逐家点击路线核验。'
          :'当前范围没有可核实的地图酒店地点，不代表整个区域没有酒店或房间。'}</p>}
        <div className="nearby-hotel-list">
          {displayed.map((point,index)=>{
            const supplier=point.source_match==='matched'&&point.source_hotel_id!==null?linked.get(point.source_hotel_id):undefined;
            const walk=walks[point.id];
            const validWalk=nearbyWalkMetres(point,walks,rankingContext,nearby.provider);
            const review=nearbyGuestReview(point);
            const used=walkAttempts.includes(point.id);
            return <article className="nearby-hotel-row" key={point.id}>
              <div className="nearby-hotel-rank" aria-label={'当前参考排序序号 '+(index+1)}>{String(index+1).padStart(2,'0')}</div>
              <div className="nearby-hotel-main">
                <strong>{point.name}</strong>
                <p>{point.city} · {point.address}</p>
                <div className="nearby-hotel-facts">
                  <span>直线约 {metres(point.straight_line_meters)}</span>
                  {nearbyPoiRating(point)!==null&&<span className="map-rating-fact">高德 POI 评分 {nearbyPoiRating(point)?.toFixed(1)}/5 · 非住客评分</span>}
                  {validWalk!==null&&<span className="walk-fact">目的地→酒店实测步行 {metres(validWalk)} · 约 {Math.ceil((walk.duration_seconds??0)/60)} 分钟 · {validWalk<=1500?'满足步行子条件':'超过1500米步行条件'}</span>}
                  {walk?.status==='complete'&&validWalk===null&&<span className="unknown-fact">旧路线证据已失效，请重新选地点核对。</span>}
                  {walk?.status==='unavailable'&&<span className="unknown-fact">步行未核实：{walk.error?.message??'来源无路线'}</span>}
                </div>
                <p className="nearby-hotel-proof">{supplier?'与本次酒店报价来源唯一同店核实，可核对其参考房价。':point.source_match==='ambiguous'?'同店对应存在歧义，不关联价格。':'仅地图酒店地点，尚未和报价来源确认同一家。'}</p>
                {review&&<p className="nearby-hotel-unknown">住客评分 {review.score.toFixed(1)}/5（{review.source}） · 住客评价 {review.review_count.toLocaleString('zh-CN')} 条 · {review.observed_at.slice(0,10)}</p>}
                {point.source_match==='unverified'&&rankingContext&&(sourceAttempted?sourceSearch?.request_id:requestId)&&<NearbyIdentityPanel
                  key={nearby.context_id+':'+(sourceSearch?.request_id??requestId)+':'+point.id}
                  contextId={nearby.context_id} anchorId={nearby.anchor_id}
                  requestId={sourceAttempted?sourceSearch!.request_id:requestId!}
                  point={point} sourceHotels={[...linked.values()]} query={sourceAttempted?sourceSearch!.context:context}
                  expiresAt={expiresAt} disabled={!!busy||offline}/>}
              </div>
              <div className="nearby-hotel-side">
                {supplier&&supplierPrice(supplier)!==null&&<div className="nearby-source-price"><small>来源参考价</small><strong>¥{supplierPrice(supplier)}</strong></div>}
                <button type="button" disabled={!!busy||used||walkAttempts.length>=3} onClick={()=>void verifyWalk(point)}>
                  {busy===point.id?'路线查询中…':used?'已尝试核对':'查步行路线'}
                </button>
              </div>
            </article>;
          })}
        </div>
        <p className="nearby-footer-note">高德POI评分不参与住客排序。只有明确许可、人工同店映射和有效评价口径才显示住客评分与评论人数；本机许可登记须人工核实合同，并不构成程序独立法律认证。真实入住人数仍未知；路线、库存及最终含费价必须分别核验。</p>
      </div>}
    </>}
  </section>;
}
