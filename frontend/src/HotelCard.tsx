import {useEffect, useRef, useState} from 'react';
import {hotelQuotes} from './api';
import HotelTransport from './HotelTransport';
import QuoteEvidence from './QuoteEvidence';
import {BookabilityGate} from './CapabilityStages';
import RoomQuoteRow from './RoomQuoteRow';
import DecisionPanel from './DecisionPanel';
import {referenceFen} from './CandidateComparison';
import {money, safeBooking, sourceEntry, stale, timeText} from './format';
import {DEMO_SOURCE_NAME} from './OfflineNotice';
import type {Hotel, QuotesResponse, SearchInput} from './types';

export default function HotelCard({hotel,requestId,queriedAt,context,sourceName='道旅',sourceId,offline=false,compareSelected,compareDisabled,onCompareToggle,onQuote}:{hotel:Hotel;requestId:string;queriedAt:string;context:SearchInput;sourceName?:string;sourceId?:string;offline?:boolean;compareSelected:boolean;compareDisabled:boolean;onCompareToggle:()=>void;onQuote?:(quote:QuotesResponse|null)=>void}) {
  const [expanded,setExpanded] = useState(false);
  const [loading,setLoading] = useState(false);
  const [data,setData] = useState<QuotesResponse|null>(null);
  const [error,setError] = useState('');
  const [visible,setVisible] = useState(20);
  const [imageFailed,setImageFailed] = useState(false);
  const [,tick] = useState(0);
  const controller = useRef<AbortController|null>(null);
  const generation = useRef(0);
  useEffect(()=>{
    const interval = window.setInterval(()=>tick(value=>value+1),30000);
    return ()=>{window.clearInterval(interval);controller.current?.abort();generation.current++;};
  },[]);
  async function load() {
    onQuote?.(null);
    controller.current?.abort();const next = new AbortController();controller.current=next;
    const current = ++generation.current;setExpanded(true);setLoading(true);setData(null);setError('');setVisible(20);
    const timeout = window.setTimeout(()=>next.abort(),65000);
    try {
      const result = await hotelQuotes(hotel.id,requestId,next.signal);
      if (generation.current !== current || next.signal.aborted) return;
      if (result.request_id!==requestId || result.hotel_id!==hotel.id || (sourceId && result.source.id!==sourceId)) throw new Error('报价条件已变化，请重新查询。');
      onQuote?.(result);
      if (result.status==='unavailable') throw new Error(result.error?.message || '暂未取得房型报价。');
      setData(result);
    } catch (reason) {
      if (generation.current!==current) return;
      setError(reason instanceof DOMException && reason.name==='AbortError' ? '查询超时，请重试。' : reason instanceof Error ? reason.message : '暂未取得房型报价。');
    } finally {window.clearTimeout(timeout);if(generation.current===current)setLoading(false);}
  }
  function toggle() {if(expanded){setExpanded(false);}else if(data){setExpanded(true);}else{void load();}}
  const score = hotel.guest_score;
  const scoreLabel = score && Number.isFinite(score.value) && score.value >= 0 &&
    Number.isFinite(score.scale) && score.scale > 0 && score.value <= score.scale && score.source.trim()
    ? `住客评分 ${score.value}/${score.scale} · ${score.source}` : null;
  const sourceLabel = offline ? DEMO_SOURCE_NAME : data?.source.name ?? sourceName;
  const booking = !offline && data?.source.id==='rollinggo' && data.booking_status==='matched' && data.occupancy_status!=='mismatch' ? safeBooking(data.booking_url) : null;
  const entry = offline || !data ? null : sourceEntry(data.source.id);
  return <article className={'hotel-card'+(expanded?' is-expanded':'')}>
    <div className="hotel-main">
      <div className="hotel-photo">
        {hotel.image_url && !imageFailed ? <img src={hotel.image_url} alt={hotel.name} loading="lazy" referrerPolicy="no-referrer" onError={()=>setImageFailed(true)}/> :
          <div className="photo-fallback"><span>栖</span><small>暂无住宿图片</small></div>}
        <span className="photo-badge">{sourceLabel} · 住宿</span>
      </div>
      <div className="hotel-body">
        <div className="hotel-category">{hotel.hotel_stars && hotel.hotel_stars>0 ? '酒店星级 '+hotel.hotel_stars : '星级未取得'}{scoreLabel&&<span>{scoreLabel}</span>}</div>
        <h3>{hotel.name}</h3><p className="address">{hotel.address || '详细地址待核实'}</p>
        <div className="hotel-bottom"><div><span className="price-caption">搜索参考价</span><div className="price">{referenceFen(hotel)!==null?<><small>¥</small>{money(hotel.reference_price_fen)}</>:<span className="unknown-price">人民币参考价待核实</span>}</div></div>
          <div className="hotel-actions"><button type="button" className="button outline compare-toggle" onClick={onCompareToggle} aria-pressed={compareSelected} disabled={compareDisabled} aria-label={(compareSelected?'移出对比：':'加入对比：')+hotel.name}>{compareSelected?'已加入对比':'加入对比'}</button><button type="button" className="button outline" onClick={toggle} aria-expanded={expanded}>{expanded?'收起房型':'查看房型'}<span aria-hidden="true">{expanded?'−':'↗'}</span></button></div></div>
        <p className="query-time">{offline?'数据集读取于':'搜索于'} {timeText(queriedAt)} · {offline?'仅为模拟搜索参考价，非真实房价':'参考价并非最终付款金额，详情以供应商为准'}</p>
      </div>
    </div>
    <HotelTransport hotel={hotel} requestId={requestId} context={context} offline={offline}/>
    {expanded && <section className="room-panel" aria-label={hotel.name+'的房型报价'}>
      <div className="room-toolbar"><div><h4>房型与报价</h4><p>{data ? '查询于 '+timeText(data.queried_at) : loading ? '正在获取新的房型报价…' : '暂未取得报价'}</p></div>
        <button type="button" className="text-button" onClick={()=>void load()} disabled={loading}>{loading?'查询中…':'↻ 刷新报价'}</button></div>
      {loading && <div className="room-loading" role="status"><span className="spinner"/>正在查询这次入住条件的房型</div>}
      {error && <div className="state-message error-state" role="alert"><strong>这次未能取得报价</strong><p>{error}</p><button type="button" className="text-button" onClick={()=>void load()}>重新获取</button></div>}
      {data && <>
        {offline ? <p className="test-banner">广州测试夹具 · 非实时查源 · 不可订</p> : data.data_mode==='test' && <p className="test-banner">测试数据 · 仅用于界面验证</p>}
        {data.status==='partial' && <p className="stale-note">部分报价未能核实或已达到展示上限，以下并非全部房型。</p>}
        {stale(data.queried_at) && <p className="stale-note">报价查询已超过5分钟，请刷新后核对。</p>}
        <div className="occupancy-note"><strong>本次请求：{data.context.check_in} — {data.context.check_out} · {data.context.rooms}间房 · 共{data.context.adults}成人{data.context.children_ages.length>0?'、儿童'+data.context.children_ages.join('/')+'岁':''}</strong><p>{data.occupancy_status==='matched'?'来源已回显人数与房间组合，但最终费用、独卫和库存仍未核实。':data.occupancy_status==='mismatch'?'来源回显的入住条件不一致；此报价不得视为满足本次人数与房数组合。':'请求已发送，但来源未完整回显人数与房间组合；不能据展示人数或价格断言两成人含费可订。'}</p></div>
        {data.booking_status==='mismatch' && <p className="handoff-warning" role="status">来源预订链接与本次入住条件不一致，已停用跳转。请在{sourceLabel}手动搜索住宿，并重新填写日期和入住人数。</p>}
        {data.booking_status==='unverified' && <p className="handoff-warning" role="status">来源预订链接未能核实完整入住条件，暂不提供跳转。请在{sourceLabel}手动核对。</p>}
        <BookabilityGate data={data}/>
        <QuoteEvidence data={data}/>
        {data.decision && <DecisionPanel data={data.decision}/>}
        <p className="quote-warning">以下每一行是{offline?'合成演示报价方案，不是真实房型或可订房量':'供应商报价方案，不是可订房量'}；{data.context.budget_fen!==null?`即使某报价的展示均价不高于¥${money(data.context.budget_fen)}参考预算，也不能证明最终含费价达标。`:'未设置预算，也不能将展示均价当作最终含费价。'}独卫、实际库存及入住人数须另行核实。</p>
        {data.quotes.length===0 ? <div className="state-message"><strong>暂未返回房型报价</strong><p>可以稍后刷新，或调整入住条件。</p></div> :
          <div className="quote-list">{data.quotes.slice(0,visible).map((quote,index)=><RoomQuoteRow key={quote.id} quote={quote} context={data.context} index={index} sourceName={sourceLabel}/>)}</div>}
        {visible<data.quotes.length && <button type="button" className="more-button" onClick={()=>setVisible(count=>count+20)}>再显示20个报价（共{data.quotes.length}个）</button>}
        {offline ? <div className="handoff"><p>广州测试夹具不提供预订跳转；当前合成报价仅用于 Agent 规则测试，可订层恒不通过。</p><span className="disabled-link">广州测试夹具 · 不提供真实预订入口</span></div> : <div className="handoff"><p>即使链接匹配，也不等于确认有房或锁价；请在{sourceLabel}逐项核实后自行决定是否预订。</p>{booking ? <a className="button primary" href={booking} target="_blank" rel="noopener noreferrer">前往{sourceLabel}核实条件 <span aria-hidden="true">↗</span></a> : <><span className="disabled-link">{data.booking_status==='unavailable'?'预订入口暂未取得':'入住条件未核实，跳转已停用'}</span>{entry && <a className="button outline" href={entry} target="_blank" rel="noopener noreferrer">打开{sourceLabel}首页自行核对 <span aria-hidden="true">↗</span></a>}</>}</div>}
      </>}
    </section>}
  </article>;
}
