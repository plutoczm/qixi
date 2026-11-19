import {useState} from 'react';
import {discoverySearch} from './api';
import {timeText} from './format';
import type {DiscoveryResponse, SearchInput} from './types';

const unavailableStates=new Set(['unavailable','unconfigured','cancelled','expired','partial','limited']);

/**
 * FlyAI candidates for the same city and dates.
 *
 * This block is deliberately separate from the quote results: it shows hotel
 * *names* only, states the display price as verbatim supplier text, and never
 * offers a booking entry. A click costs exactly one supplier call, so it never
 * runs on its own.
 */
export default function DiscoveryPanel({context,offline=false}:{context:SearchInput;offline?:boolean}) {
  const [data,setData]=useState<DiscoveryResponse|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  async function load(){
    if(loading)return;
    const controller=new AbortController();
    const timer=window.setTimeout(()=>controller.abort(),60000);
    setLoading(true);setError('');
    try{
      const result=await discoverySearch(context,controller.signal);
      setData(result);
      if(unavailableStates.has(result.status))setError(result.error?.message??(offline?'演示候选名单暂不可用。':'飞猪本次未返回可用候选。'));
    }catch(reason){
      setError(reason instanceof DOMException&&reason.name==='AbortError'?(offline?'读取演示候选超时。':'飞猪查询超时，未取得候选。'):reason instanceof Error?reason.message:(offline?'演示候选读取未完成。':'飞猪查询未完成。'));
    }finally{window.clearTimeout(timer);setLoading(false);}
  }
  return <section className="discovery-panel" aria-label={offline?'模拟候选名单':'飞猪候选名单'}>
    <div className="discovery-heading">
      <div><h4>{offline?'广州测试夹具候选名单':'其他来源候选 · 飞猪'}</h4><p>{offline?'与报价物理分开的模拟候选：没有房型、库存、入住回显与预订链接，不合并或排序，读取不产生外部请求。':'只提供酒店名称级参考：没有房型、库存、入住回显与预订链接，不与上方报价合并或排序。'}</p></div>
      <button type="button" className="button outline" onClick={()=>void load()} disabled={loading}>
        {loading?(offline?'正在读取模拟候选…':'正在查询飞猪候选…'):data?(offline?'重新读取模拟候选':'重新查询飞猪候选（1 次请求）'):(offline?'读取模拟候选（0 次请求）':'查询飞猪候选（1 次请求）')}
      </button>
    </div>
    {error&&<p className="discovery-error" role="alert">{error}</p>}
    {data&&<>
      <p className="discovery-meta">
        {offline?'广州测试夹具':data.source.name} · {timeText(data.queried_at)} · 返回 {data.candidates.length} 个候选名称
        {!offline&&data.observed_at?` · 来源观察于 ${timeText(data.observed_at)}`:''}
      </p>
      {data.candidates.length>0?<ul className="discovery-list">
        {data.candidates.map((candidate,index)=><li key={candidate.name+':'+index}>
          <div>
            <strong>{candidate.name}</strong>
            <small>{candidate.address||'地址未返回'}</small>
          </div>
          <div className="discovery-facts">
            {candidate.star&&<span>{candidate.star}</span>}
            {candidate.nearby_transit&&<span>{candidate.nearby_transit}（描述，不代表距离）</span>}
            {candidate.price_text
              ?<span className="discovery-price">来源展示价原文“{candidate.price_text}” · 币种与口径未注明</span>
              :<span className="discovery-price">来源未展示价格</span>}
            <span>{candidate.coordinates_present?'来源带坐标（本页不用于距离或路线）':'来源未返回坐标'}</span>
          </div>
        </li>)}
      </ul>:<p className="discovery-empty">本次没有返回候选名称；这不代表该城市没有住宿。</p>}
      <ul className="discovery-limits">
        {data.limitations.map(line=><li key={line}>{line}</li>)}
      </ul>
    </>}
    {!data&&!error&&<p className="discovery-hint">{offline?'这是与报价分栏的固定模拟候选，读取不产生外部请求。':'飞猪是另一个来源，只在点击后发起一次查询，不会自动运行，也不影响当前报价。'}</p>}
  </section>;
}
