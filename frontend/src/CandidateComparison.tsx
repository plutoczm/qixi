import {money,stale,timeText} from './format';
import type {Hotel} from './types';

export type ComparisonItem = {hotel: Hotel; observedAt: string};

export function referenceFen(hotel: Hotel): number | null {
  const amount=hotel.reference_price_fen;
  return hotel.currency==='CNY' && amount!==null && Number.isSafeInteger(amount) && amount>=0
    ? amount : null;
}

export function referenceDelta(items: readonly ComparisonItem[]): Map<number,number> | null {
  // A missing/non-CNY price invalidates a comparable *group* difference.
  // Never silently interpret missing data as zero or convert currencies.
  if(items.length<2 || items.length>3 || items.some(item=>referenceFen(item.hotel)===null))return null;
  const lowest=Math.min(...items.map(item=>referenceFen(item.hotel)!));
  return new Map(items.map(item=>[item.hotel.id,referenceFen(item.hotel)!-lowest]));
}

export default function CandidateComparison({items,onClear,onRemove,isTest,offline=false}:{
  items: readonly ComparisonItem[];
  onClear:()=>void;
  onRemove:(hotelId:number)=>void;
  isTest:boolean;
  offline?:boolean;
}) {
  if(items.length<2 || items.length>3)return null;
  const delta=referenceDelta(items);
  const rows=[
    {name:'搜索参考价',value:(item:ComparisonItem)=>{
      const price=referenceFen(item.hotel);
      return price===null?'人民币参考价未取得':'¥'+money(price);
    }},
    {name:'相对所选最低参考价',value:(item:ComparisonItem)=>{
      const gap=delta?.get(item.hotel.id);
      return gap===undefined?'不可比较':'+'+money(gap)+'元（仅参考差额）';
    }},
    {name:'酒店地址',value:(item:ComparisonItem)=>item.hotel.address||'详细地址未取得'},
    {name:'酒店星级',value:(item:ComparisonItem)=>item.hotel.hotel_stars!==null&&item.hotel.hotel_stars>0
      ? String(item.hotel.hotel_stars)+'星（不是住客评分）':'未取得（不是住客评分）'},
    {name:offline?'数据集读取时间':'来源查询时间',value:(item:ComparisonItem)=>timeText(item.observedAt)+
      (Number.isNaN(Date.parse(item.observedAt))?' · 时效未核实':stale(item.observedAt)?' · 已超过5分钟':'')},
    {name:'预订资格',value:()=> '未核实 · 需另查房型、入住人数、独卫、库存及含费总价'},
    {name:'同店与交通',value:()=> '请单独核对门店身份和新鲜路线'},
  ];
  return <section className="candidate-comparison" aria-label="酒店参考候选对比" tabIndex={-1}>
    <div className="candidate-comparison-head">
      <div><h3>参考候选对比 · {items.length}家</h3><p>同一查询条件下的酒店搜索参考价；仅浏览，不对比可订实付总价或推荐合格最低价。</p></div>
      <button type="button" className="text-button" onClick={onClear}>清空对比</button>
    </div>
    {(isTest||offline)&&<p className="test-banner">模拟数据 · 本次比较不能作为真实房价证据</p>}
    <div className="candidate-comparison-scroll" role="region" tabIndex={0} aria-label="横向滚动查看酒店对比">
      <table>
        <thead><tr><th scope="col">对比项目</th>{items.map(item=><th key={item.hotel.id} scope="col">
          <span>{item.hotel.name}</span>
          <button type="button" className="text-button" onClick={()=>onRemove(item.hotel.id)}
            aria-label={'移出对比：'+item.hotel.name}>移出</button>
        </th>)}</tr></thead>
        <tbody>{rows.map(row=><tr key={row.name}><th scope="row">{row.name}</th>
          {items.map(item=><td key={item.hotel.id}>{row.value(item)}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
    <p className="candidate-comparison-note">{offline?'所有候选均来自同一固定合成数据集；差额仅演示规则，不代表真实房价或可订价格。':'不同酒店的来源观察时间可能不同；价格可能变化，超过5分钟须重新查询才有更新证据。'}仅对完整有效的人民币参考价计算差额，不自动查房型、刷新、查路线或发送任何请求。</p>
  </section>;
}
