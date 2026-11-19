import {useEffect, useRef, useState} from 'react';
import useCinematicMotion from './useCinematicMotion';
import {health, searchHotels,loadTuniuPage,recallHotels,cancelRecall,continueRecall,parallelSearchHotels,cancelParallelSearch} from './api';
import {timeText} from './format';
import SearchForm from './SearchForm';
import {mergeRecallContinuation} from './mergeRecallContinuation';
import type {RecallOptions} from './SearchForm';
import RecallCoverage from './RecallCoverage';
import BranchWalkPanel from './BranchWalkPanel';
import NaturalQuery from './NaturalQuery';
import type {TripHandoff} from './tripIntent';
import HotelResults from './HotelResults';
import NearbyExplorer from './NearbyExplorer';
import HotelSearchSkeleton from './HotelSearchSkeleton';
import DiscoveryPanel from './DiscoveryPanel';
import stayLandscape from './assets/stay-landscape.svg';
import OfflineNotice, {DEMO_SOURCE_NAME, isOffline} from './OfflineNotice';
import CheckpointNotice from './CheckpointNotice';
import ParallelHotelResults from './ParallelHotelResults';
import type {CheckpointResume, ConversationState, Health, ParallelSearchResponse, SearchInput, SearchResponse, RecallResponse} from './types';

export default function App() {
  const [result,setResult] = useState<SearchResponse|null>(null);
  const [batch,setBatch]=useState<ParallelSearchResponse|null>(null);
  const [loading,setLoading] = useState(false);
  const [continuing,setContinuing]=useState(false);
  const [error,setError] = useState('');
  const [failedAttempt,setFailedAttempt]=useState<{input:SearchInput;options?:RecallOptions}|null>(null);
  const [configured,setConfigured] = useState<boolean|null>(null);
  const [healthData,setHealthData] = useState<Health|null>(null);
  const [providerName,setProviderName] = useState<'道旅'|'途牛'|'广州测试夹具'>('道旅');
  const [resumeConversation,setResumeConversation]=useState<ConversationState|null>(null);
  const offline=isOffline(healthData);
  const parallelEnabled=!offline&&healthData?.features?.parallel_search===true;
  const [preset,setPreset] = useState<SearchInput>();
  const [trip,setTrip]=useState<TripHandoff|null>(null);
  const [multi,setMulti]=useState<RecallResponse|null>(null);const [branch,setBranch]=useState('');
  const recallOperation=useRef<string|null>(null);
  const parallelOperation=useRef<string|null>(null);
  const pendingHealth=useRef<Promise<Health>|null>(null);
  const sequence = useRef(0);
  const active = useRef<AbortController|null>(null);
  useEffect(()=>{
    let mounted=true;
    const controller=new AbortController();
    const timer=window.setTimeout(()=>controller.abort(),8000);
    const ticket=health(controller.signal);
    pendingHealth.current=ticket;
    void ticket.then(value=>{if(mounted)applyHealth(value);}).catch(()=>{}).finally(()=>{
      window.clearTimeout(timer);
      if(pendingHealth.current===ticket)pendingHealth.current=null;
    });
    return ()=>{mounted=false;controller.abort();sequence.current++;active.current?.abort();if(recallOperation.current)void cancelRecall(recallOperation.current).catch(()=>{});stopParallel();};
  },[]);
  function applyHealth(value:Health){
    setConfigured(value.credential_configured);setHealthData(value);
    setProviderName(value.data_mode==='offline'?DEMO_SOURCE_NAME:value.provider==='rollinggo'?'道旅':'途牛');
  }
  function stopParallel(){
    const operation=parallelOperation.current;
    parallelOperation.current=null;
    if(operation)void cancelParallelSearch(operation).catch(()=>{});
  }
  async function search(input:SearchInput,options?:RecallOptions) {
    if(recallOperation.current)void cancelRecall(recallOperation.current).catch(()=>{});
    stopParallel();setBatch(null);
    let parallelId:string|null=null;
    setContinuing(false);
    const operation=options?crypto.randomUUID():null;recallOperation.current=operation;setMulti(null);setBranch('');
    active.current?.abort();const controller=new AbortController();active.current=controller;
    const current=++sequence.current;setLoading(true);setResult(null);setError('');setFailedAttempt(null);
    const resultsPanel=document.getElementById('results-panel');
    if(resultsPanel && typeof resultsPanel.scrollIntoView==='function'){
      const reduceMotion=typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      resultsPanel.scrollIntoView({behavior:reduceMotion?'auto':'smooth',block:'start'});
    }
    const timeout=window.setTimeout(()=>{controller.abort();if(operation)void cancelRecall(operation).catch(()=>{});if(parallelId&&parallelOperation.current===parallelId)stopParallel();},healthData||options?65000:73000);
    try {
      if(!options){
        const backend=healthData??await (pendingHealth.current??health(controller.signal));
        if(sequence.current!==current||controller.signal.aborted)return;
        if(!healthData)applyHealth(backend);
        if(!isOffline(backend)&&backend.features?.parallel_search===true){
          parallelId=crypto.randomUUID();
          parallelOperation.current=parallelId;
        }
      }
      if(parallelId){
        const response=await parallelSearchHotels(input,parallelId,controller.signal);
        if(sequence.current!==current||controller.signal.aborted)return;
        if(response.operation_id!==parallelId||response.status==='cancelled')throw new Error('本次查询已取消或条件已变化。');
        setBatch(response);
        return;
      }
      const response=options&&operation ? await recallHotels({query:input,operation_id:operation,...options},controller.signal):await searchHotels(input,controller.signal);
      if(sequence.current!==current || controller.signal.aborted)return;
      if(options)setMulti(response as RecallResponse);
      if(response.status==='unavailable')throw new Error(response.error?.message || '暂未取得可用结果。');
      setResult(response);
    } catch(reason) {
      if(sequence.current!==current)return;
      setError(reason instanceof DOMException && reason.name==='AbortError' ? '查询超时，请稍后手动重试。' : reason instanceof Error ? reason.message : '暂时无法查询，请手动重试。');
      setFailedAttempt({input,options});
    } finally {window.clearTimeout(timeout);if(sequence.current===current){setLoading(false);recallOperation.current=null;parallelOperation.current=null;}}
  }
  async function loadNextPage(){
    const previous=result;
    if(!previous||multi||previous.source.id!=='tuniu'||previous.next_page===null||loading)return;
    const expectedPage=previous.next_page;
    const current=++sequence.current;
    active.current?.abort();
    const controller=new AbortController();active.current=controller;
    setLoading(true);setError('');
    const timer=window.setTimeout(()=>controller.abort(),65000);
    try{
      const next=await loadTuniuPage(previous.request_id,expectedPage,controller.signal);
      if(sequence.current!==current||controller.signal.aborted)return;
      if(next.source.id!=='tuniu'||next.request_id!==previous.request_id
        ||next.current_page!==expectedPage)throw new Error('分页来源会话不一致，已停止展示。');
      if(next.status==='unavailable')throw new Error(next.error?.message??'途牛后续页未完成。');
      setResult(next);
    }catch(reason){
      if(sequence.current!==current)return;
      // Once attempted, the backend refuses a replay of this page token.
      setResult(old=>old?.request_id===previous.request_id?{...old,next_page:null}:old);
      setError(reason instanceof Error?reason.message:'后续页未能完成，已保留原结果。');
    }finally{
      window.clearTimeout(timer);
      if(sequence.current===current)setLoading(false);
    }
  }
  async function continueSearch(){
    if(!multi?.continuation_cursor||loading)return;
    const previous=multi;const current=++sequence.current;
    active.current?.abort();const controller=new AbortController();active.current=controller;
    const operation=crypto.randomUUID();recallOperation.current=operation;
    setLoading(true);setContinuing(true);setError('');
    const timer=window.setTimeout(()=>{controller.abort();void cancelRecall(operation).catch(()=>{});},65000);
    try{
      const next=await continueRecall({request_id:previous.request_id,continuation_cursor:previous.continuation_cursor!,operation_id:operation},controller.signal);
      if(sequence.current!==current||controller.signal.aborted)return;
      const merged=mergeRecallContinuation(previous,next,operation);
      setMulti(merged);setResult(merged);
      if(next.error)setError('本轮续查部分未完成，原结果保留：'+next.error.message);
    }catch(reason){
      if(sequence.current===current){
        setError('续查未完成，原结果保留。'+(reason instanceof Error?reason.message:'请核对后再操作。'));
        if(reason instanceof Error&&'code' in reason&&['CONTINUATION_STALE','CONTINUATION_EXPIRED','CONTINUATION_NOT_FOUND','CONTINUATION_INVALID_DELTA'].includes(String(reason.code)))setMulti(old=>old?{...old,continuation_cursor:null}:old);
      }
    }finally{
      window.clearTimeout(timer);
      if(sequence.current===current){setLoading(false);setContinuing(false);recallOperation.current=null;}
    }
  }
  function stopSearch(){sequence.current++;active.current?.abort();stopParallel();if(recallOperation.current)void cancelRecall(recallOperation.current).catch(()=>{});recallOperation.current=null;setLoading(false);setContinuing(false);setFailedAttempt(null);setError(continuing?'已停止续查，原结果保留；已发出的查名不会自动重试。':'已请求停止；正在进行的请求可能仍在结束。');}
  function applyNatural(input:SearchInput,plannedTrip:TripHandoff) {
    setTrip(plannedTrip);
    stopParallel();setBatch(null);
    if(recallOperation.current)void cancelRecall(recallOperation.current).catch(()=>{});recallOperation.current=null;setMulti(null);setBranch('');
    sequence.current++;active.current?.abort();setLoading(false);setContinuing(false);setResult(null);setError('');setFailedAttempt(null);setPreset({...input});
    const drawer=document.getElementById('assistant-panel') as HTMLDetailsElement|null;
    if(drawer)drawer.open=false;
    const form=document.getElementById('query-panel');
    if(form&&typeof form.scrollIntoView==='function')form.scrollIntoView({behavior:'smooth',block:'start'});
  }
  function applyCheckpoint(data:CheckpointResume) {
    if(data.status!=='ready')return;
    sequence.current++;active.current?.abort();
    if(data.search){setResult(data.search);setPreset(data.search.context);setTrip(null);setMulti(null);setBranch('');setError('');setFailedAttempt(null);setLoading(false);}
    if(data.conversation)setResumeConversation(data.conversation);
  }
  function expandCoverage() {
    if(!result)return;if(multi?.strategy==='named_hotel'){setError('请返回表单修改点名酒店与餐厅身份，再手动提交新查询。');return;}const input={...result.context,limit:20};setPreset(input);void search(input,multi?{provider:multi.map_provider,restaurant_keyword:multi.restaurant_keyword,branch_limit:multi.recall_coverage.branch_limit as 3|5,strategy:multi.strategy,named_hotel:null,restaurant_address:null}:undefined);
  }
  function clearBudget() {
    if(!result)return;if(multi?.strategy==='named_hotel'){setError('点名酒店模式需在表单重新核对身份后提交。');return;}const input={...result.context,budget_fen:null};setPreset(input);void search(input,multi?{provider:multi.map_provider,restaurant_keyword:multi.restaurant_keyword,branch_limit:multi.recall_coverage.branch_limit as 3|5,strategy:multi.strategy,named_hotel:null,restaurant_address:null}:undefined);
  }
  const visibleHotels=result?.hotels.filter(hotel=>!branch||!!multi?.branches.find(item=>item.id===branch)?.hotel_ids.includes(hotel.id))??[];
  const mapSearch=batch?.sources.find(source=>source.id==='rollinggo'&&source.search?.hotels.length)?.search
    ??batch?.sources.find(source=>source.search?.hotels.length)?.search;
  const shell=useRef<HTMLDivElement>(null);
  useCinematicMotion(shell);
  return <div className="app-shell" ref={shell}>
    <header className="site-header">
      <a className="brand" href="/" aria-label="寻栖 首页"><span className="brand-mark" aria-hidden="true">栖</span><span>寻栖<small>住得舒心，走得从容</small></span></a>
      <nav className="top-nav" aria-label="主导航"><a href="#query-panel">找酒店</a><a href="#results-panel">住宿选择</a><a href="#travel-guide">出行帮助</a></nav>
      <a className="nav-cta" href="#query-panel">开始搜索 <span aria-hidden="true">↗</span></a>
    </header>
    <OfflineNotice health={healthData}/>
    {offline&&<CheckpointNotice offline={offline} onResume={applyCheckpoint}/>}
    <section className="hero consumer-hero" aria-label="寻栖住宿搜索">
      <div className="hero-layout">
        <div className="hero-copy">
          <span className="hero-kicker"><span className="hero-kicker-dot" aria-hidden="true"/> 旅途中的每一晚，都值得认真挑选</span>
          <h1>找到好住处，<br/><em>旅途更自在。</em></h1>
          <p>{offline?'体验从查找酒店到比较房型、规划返程的完整流程。这里的价格与住宿为演示内容。':'从中国大陆的城市、地标或具体门店出发，按预算找住宿，再核对附近距离与真实房型信息。'}</p>
          <div className="hero-actions">
            <a href="#query-panel" className="hero-primary">开始搜索 <span aria-hidden="true">↗</span></a>
            <a href="#assistant-panel" className="hero-secondary" onClick={()=>{const panel=document.getElementById('assistant-panel') as HTMLDetailsElement|null;if(panel)panel.open=true;}}>让 AI 帮我填 <span aria-hidden="true">↓</span></a>
          </div>
          <div className="hero-benefits"><span>查找酒店</span><span>比较房型</span><span>规划返程</span></div>
        </div>
        <div className="hero-scene" aria-hidden="true">
          <span className="scene-index">YOUR NEXT STAY <span>✦</span></span>
          <div className="scene-picture" data-scene-depth="far"><img src={stayLandscape} alt=""/></div><div className="scene-atmosphere" data-scene-depth="near"/><div className="scene-map-lines"/>
          <div className="scene-note"><span>FEEL AT HOME</span><strong>下一站，<br/>住得刚刚好。</strong></div>
          <div className="scene-seal"><span>栖</span><small>FIND YOUR<br/>PLACE</small></div>
        </div>
      </div>
    </section>
    <main className="consumer-main">
      <section className="booking-zone" id="query-panel" aria-label="查找酒店">
        <SearchForm loading={loading} preset={preset} trip={trip} sourceName={parallelEnabled?'道旅、途牛、飞猪':providerName} offline={offline} onSubmit={search} onRecall={!offline&&providerName==='道旅'?search:undefined}/>
        <details className="assistant-drawer" id="assistant-panel">
          <summary><span className="assistant-spark" aria-hidden="true">✧</span><span><strong>AI 帮我整理入住需求</strong><small>不用一次说全，跟着提示一步步补充入住条件</small></span><span className="assistant-arrow" aria-hidden="true">展开 ↓</span></summary>
          <NaturalQuery onApply={applyNatural} offline={offline} resumeState={resumeConversation}/>
        </details>
        {!offline&&!parallelEnabled&&configured===false&&<p className="config-warning" role="status">酒店搜索服务暂时不可用，请稍后再试。</p>}
      </section>
      <div className="value-strip" id="travel-guide" aria-label="寻栖可以做什么">
        <div data-scroll-reveal><span className="value-icon" aria-hidden="true">⌕</span><div><strong>查找合适的酒店</strong><p>按目的地、日期与预算缩小选择</p></div></div>
        <div data-scroll-reveal><span className="value-icon" aria-hidden="true">⌂</span><div><strong>看清房型与费用</strong><p>比较参考价格，了解需要确认的条件</p></div></div>
        <div data-scroll-reveal><span className="value-icon" aria-hidden="true">↗</span><div><strong>提前规划返程</strong><p>需要时再查看步行路线与当前车费参考</p></div></div>
      </div>
      <section className="results" id="results-panel" aria-label="住宿搜索结果">
      <div className="results-heading" data-reveal><div><span className="section-kicker">HOTELS & STAYS</span><h2>{result||batch?'看看这次找到的住宿':'为你的下一站，挑一间好住处'}</h2>{(result||batch)&&<a href="#query-panel" className="edit-search-link">修改搜索</a>}</div>{result&&<span className="source-label">价格来源：{offline?DEMO_SOURCE_NAME:result.source.name}</span>}</div>
      {multi&&<RecallCoverage data={multi} selected={branch} onSelect={setBranch} onContinue={()=>void continueSearch()} loading={loading}/>}
      {multi&&multi.hotels.length>0&&!continuing&&<BranchWalkPanel key={multi.request_id+':'+multi.revision} data={multi}/>}
      {loading&&<button type="button" className="button outline stop-search" onClick={stopSearch}>停止查询</button>}
      {loading && <div className="state-message loading-state" role="status"><span className="spinner large"/><h3>{continuing?'正在查找更多酒店':offline?'正在读取演示住宿':'正在为你查找酒店'}</h3><p>请稍候，找到后就可以查看房型和价格。</p></div>}
      {loading && !result && !continuing && <HotelSearchSkeleton/>}
      {error && <div className="state-message error-state" role="alert"><span className="state-icon">!</span><h3>抱歉，这次搜索没有完成</h3><p>{error}</p><p className="quiet">你填写的搜索条件仍在，可以检查后再次尝试。</p>
        {failedAttempt && !loading && !result && <div className="manual-retry">
          <p>上次条件：{failedAttempt.input.place} · {failedAttempt.input.check_in} 至 {failedAttempt.input.check_out}。再次调用来源将消耗个人 API 请求额度，不会自动重试。</p>
          <div className="manual-retry-actions"><button className="button primary" type="button" onClick={()=>void search(failedAttempt.input,failedAttempt.options)}>{offline?'按上次条件重新读取演示':'按上次条件重新查询（消耗 API 请求）'}</button><a href="#query-panel">先修改搜索条件</a></div>
        </div>}
      </div>}
      {!result && !batch && !loading && !error && <div className="welcome-state" data-scroll-reveal><div className="welcome-symbol" aria-hidden="true"><span>⌂</span></div><h3>从这里开始，找到喜欢的酒店</h3><p>在上方填好目的地、入住日期和人数，点击“查询房价”，这里就会出现可供比较的住宿。</p><div className="welcome-points"><span>查看酒店</span><span>比较价格</span><span>了解返程</span></div></div>}
      {batch&&<div key={batch.operation_id}>
        <div className="condition-summary"><strong>{batch.context.place}</strong><span>{batch.context.check_in} — {batch.context.check_out}</span><span>每房 {batch.context.adults} 成人 · {batch.context.rooms} 间房</span></div>
        <NearbyExplorer requestId={mapSearch?.request_id??null} context={batch.context} hotels={mapSearch?.hotels??[]} offline={false} parallel/>
        <ParallelHotelResults batch={batch}/>
      </div>}
      {result && <>
        {offline && <p className="test-banner">广州测试夹具 · 非实时查源 · 不可订</p>}
        {!offline&&result.data_mode==='test' && !multi && <p className="test-banner">测试数据 · 仅用于界面验证</p>}
        <p className="booking-caveat">以下为查询得到的住宿与参考价格。查看房型时可进一步核对入住人数、费用与取消政策；参考价不等于最终预订价。</p>
        <div className="condition-summary" data-reveal><strong>{result.context.place}</strong><span>{result.context.check_in} — {result.context.check_out}</span><span>每房{result.context.adults}成人{result.context.children_ages.length>0?'、'+result.context.children_ages.length+'儿童':''} · {result.context.rooms}间房</span><small>{offline?'数据集读取于':multi&&multi.round_number>1?'最近一轮查询于':'查询于'} {timeText(result.queried_at)}</small></div>
        <div className="result-meta"><span>{offline?'本次数据集返回':'本次查询返回'} <strong>{result.hotels.length}</strong> 家住宿</span><span>{multi?.strategy==='named_hotel'?'指定酒店核验 · 来源参考价':multi?'门店轮转并集 · 首次召回参考价':offline?'固定数据集顺序 · 模拟参考价':'平台返回顺序 · 搜索参考价'}</span></div>
        {!multi&&<NearbyExplorer key={result.request_id} requestId={result.request_id} context={result.context} hotels={result.hotels} offline={offline}/>}
        <details className="search-scope"><summary>搜索范围与说明</summary><p className="coverage-note">{multi?(multi.strategy==='named_hotel'?'此轮只核对用户指定的一家完整名称与地址；若无唯一地图和{result.source.name}同店不返回其他酒店。':multi.strategy==='map_hotels'?'每次查名最多返回'+result.context.limit+'条，每轮最多5次；本次保留'+result.hotels.length+'家待核对住宿。':'每门店查询上限'+result.context.limit+'家，本次并集'+result.hotels.length+'家；同店跨门店只显示一次。'):'本次查询上限'+result.context.limit+'家，预算筛选后可能更少；不代表全城完整结果。'}</p></details>
        {result.status==='partial' && <p className="stale-note">部分来源信息未能核实，以下展示本次已取得的结果。</p>}
        {visibleHotels.length===0 ? <div className="state-message empty-state"><span className="state-icon">⌕</span><h3>{!multi&&result.source.id==='tuniu'&&result.status==='partial'&&result.coverage.limited&&result.hotels.length===0?'途牛首页无候选，但仍有未查询的来源页':'这次条件下，暂未查到住宿'}</h3><p>{!multi&&result.source.id==='tuniu'&&result.status==='partial'&&result.coverage.limited&&result.hotels.length===0?'本轮没有自动翻页，不能断定'+result.context.place+'没有符合条件的酒店。重新搜索会消耗途牛请求额度。':'可以调整日期或地点；空结果不代表整个城市没有合适住宿。'}</p>{multi?.strategy!=='named_hotel'&&result.context.limit<20 && <button className="button outline" type="button" onClick={expandCoverage}>扩大到20家再查</button>}{multi?.strategy!=='named_hotel'&&result.context.budget_fen!==null && <button className="button outline" type="button" onClick={clearBudget}>清除预算再查一次</button>}</div> :
          <div data-reveal><HotelResults key={result.request_id+':'+(multi?.revision??0)+':'+branch} result={result} multi={multi} hotels={visibleHotels} offline={offline}/></div>}
        <details className="discovery-drawer"><summary>还想看看其他住宿线索？</summary><DiscoveryPanel key={result.request_id+':'+result.context.check_in+':'+result.context.place} context={result.context} offline={offline}/></details>
        {!offline&&!multi&&result.source.id==='tuniu'&&result.next_page!==null&&result.status!=='unavailable'&&<div className="coverage-note">
          <p>途牛已查看第 {result.current_page} 页，仍有未查来源页。每次点击只查询下一页，不会自动重试、翻页或更改预算；会消耗途牛共享请求额度（最多加载至第3页）。</p>
          <button className="button outline" type="button" disabled={loading} onClick={()=>void loadNextPage()}>
            手动查看途牛第 {result.next_page} 页（消耗请求额度）
          </button>
        </div>}
        <div className="result-disclaimer"><strong>报价说明</strong><p>{offline?'这里展示的是广州离线测试夹具中的合成参考价与样例房型，用于测试资格、费用和排序规则；可订层恒不通过，不提供真实供应商跳转。':`搜索价是平台参考价，口径未完全核实；房型价是平台展示均价，均未锁价。独立客房、独卫、总价、税费与库存仍需在${result.source.name}确认；没有住客评分时不设置评分门槛。`}</p></div>
      </>}
    </section></main>
    <footer className="consumer-footer">
      <div className="footer-lead"><strong className="footer-brand">寻栖</strong><span>找到适合的住处，安心享受旅途。</span></div>
      <details className="footer-disclosure"><summary>价格与预订说明</summary><p>平台展示价格会随日期与房型变化。查询结果用于比较参考，实际房量、取消政策和最终费用以供应商核实结果为准。寻栖不代为下单。</p></details>
    </footer>
  </div>;
}
