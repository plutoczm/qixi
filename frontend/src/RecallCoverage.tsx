import {useEffect,useState} from 'react';
import {timeText} from './format';
import type {RecallResponse} from './types';
const reasons:Record<string,string>={branch_budget:'门店查询名额已用完',restaurant_role_unknown:'堂食角色未知，未自动查住宿',restaurant_role_auxiliary:'外送／附属点，未自动查住宿',source_city_unverified:'道旅未独立回显酒店所在城市；名址一致仍需确认同店',source_address_mismatch:'名称相同但道旅地址不同，请核对城市与具体房源',source_name_mismatch:'地址相同但道旅酒店名称不同，请核对身份',location_unverified:'地址或城市未核实',cancelled:'已请求停止',deadline:'达到查询截止',quote_source_blocked:'报价来源受限，未继续',lookup_budget:'本轮5次查名预算已用完',map_source_blocked:'地图来源受限，未继续',nearby_source_failure:'门店周边地点查询失败',no_valid_map_hotels:'本次未取得可核对的酒店地点',other_source_hotel:'返回其他酒店，已排除',multiple_source_identities:'返回多家可能同店，未选择',identity_conflict:'住宿编号或地图酒店身份冲突，已排除',attempt_interrupted:'此前查名未提交结果，不自动重复查价',named_restaurant_identity_mismatch:'餐厅完整名址未匹配本次堂食地图本体，已停止',named_restaurant_not_unique:'相同名称与地址的堂食门店不唯一，拒绝自动选择',named_map_not_queried:'尚未执行点名酒店地图查询',named_hotel_map_unavailable:'地图点名酒店检索失败，没有查道旅',named_map_identity_not_found:'酒店完整名址、城市或住宿类别不满足同店核验，未查道旅',named_map_page_limited:'指定酒店地图检索结果达到单页上限，身份可能不唯一',named_map_ambiguous:'同名同址酒店POI超过一条，未自动选用',named_supplier_lookup_reserved:'道旅查名已申请，未据此认定同店',named_supplier_unavailable:'道旅来源失败，房型未知',named_supplier_identity_mismatch:'道旅结果缺少完整同名同址酒店，其他分店已排除',named_supplier_ambiguous:'道旅有多个完整同名同址编号，未自动合并'};
const discovery:Record<string,string>={invalid_page:'本页地点无法解析，已停止',exhausted:'本次来源分页结束，仍不保证全城全量',page_limit:'达到2页上限，仍可能有未发现门店',repeated_page:'检测到重复分页，已停止',source_failure:'地图分页失败，保留已取得门店',cancelled:'已请求停止',deadline:'达到60秒截止'};
const targetStatus={not_queried:'未查',empty:'来源空返回',unavailable:'查询失败',excluded:'其他酒店已排除',ambiguous:'身份有歧义',retained:'保留待核对'};
const statuses={complete:'已查询',partial:'部分返回',unavailable:'查询失败',skipped:'未查询'};
export default function RecallCoverage({data,selected,onSelect,onContinue,loading=false}:{data:RecallResponse;selected:string;onSelect:(id:string)=>void;onContinue?:()=>void;loading?:boolean}) {
 const c=data.recall_coverage;
 const [,tick]=useState(0);
 useEffect(()=>{const timer=window.setInterval(()=>tick(v=>v+1),15000);return()=>window.clearInterval(timer);},[]);
 const expired=!!data.continuation_expires_at&&Date.now()>=Date.parse(data.continuation_expires_at);
 return <section className="recall-coverage" aria-label="多门店覆盖记录">
  {data.data_mode==='test'&&<p className="test-banner">测试数据 · 仅用于界面验证</p>}
  <div className="recall-heading"><h3>门店覆盖与候选</h3><span>{data.map_provider==='amap'?'高德':'腾讯'} · {data.restaurant_keyword} · {data.strategy==='named_hotel'?'用户点名酒店核验':data.strategy==='map_hotels'?'地图酒店反查':'门店地点检索'}</span></div>
  <p>检索{c.pages_requested}页，取得{c.unique_places}个去重地图地点；其中已识别餐厅本体{c.role_restaurant_bodies}个、附属点{c.role_auxiliary}个、未知{c.role_unknown}个；已尝试{c.queried_branches}家，名额{c.branch_limit}家。</p>
  {data.strategy==='named_hotel'&&<p className="recall-limit">已按完整名称和地址核验指定餐厅；酒店独立地图检索{c.named_hotel_map_lookups}次、严格地图同店结果{c.named_hotel_map_matches}条、道旅查名{c.hotel_lookups_attempted}次。此模式不受周边前10条排序限制，不分页、不续查、不借用同楼其他运营商的报价。地图同店不等于道旅城市已核实，房型/库存/独卫仍未知。</p>}
  {data.strategy==='map_hotels'&&<p className="recall-limit">已尝试{c.nearby_branches_attempted}家门店的地图周边地点；本轮查名{c.hotel_lookups_attempted}/{c.hotel_lookup_limit}次，累计{c.total_hotel_lookups}次（第{data.round_number}轮），{c.lookup_reuses}次复用本轮同名结果；{c.unqueried_targets}处酒店未查，{c.excluded_source_hotels}条来源关联已排除。地图酒店不等于道旅可订住宿。</p>}
  {data.strategy==='map_hotels'&&<div className="continuation-controls">
   {data.continuation_cursor&&onContinue&&<button type="button" className="button outline" disabled={loading||expired} onClick={onContinue}>继续查下一轮（最多5次）</button>}
   {expired&&<p className="stale-note">续查队列已过期，请重新查询。</p>}
   <p className="quiet">地图队列来自{data.plan_queried_at?timeText(data.plan_queried_at):'本次检索'}；继续不延长有效期，旧参考价保留原时间。位置核对仍需重新查点，不自动后台续查。</p>
  </div>}
  <p>来源返回{data.branches.filter(b=>b.status==='complete'||b.status==='partial').length}家门店、查询失败{data.branches.filter(b=>b.status==='unavailable').length}家、未查询{data.branches.filter(b=>b.status==='skipped').length}家。下方列表可滚动查看全部记录。</p>
  <p className="recall-limit">{discovery[c.discovery_stop_reason]??'覆盖情况待核实'}。{c.invalid_places>0&&'另有'+c.invalid_places+'条地点记录无法解析。'}不把检索关联当作已验证步行距离。</p>
  <p className="handoff-warning">本住宿场景默认查堂食；仅有名称与来源类别共同支持时自动查询。未知地点需人工核实本体和用途，附属点不自动参与；即使角色已识别，当前营业仍未核实。</p>
  <button type="button" className="text-button" aria-pressed={!selected} onClick={()=>onSelect('')}>查看候选并集（{data.hotels.length}家）</button>
  <div className="branch-records">{data.branches.map(branch=><div className="branch-record" key={branch.id}>
   <div><strong>{branch.name}</strong><p>{branch.address??'地址待核实'}</p><p className="quiet">角色：{branch.role==='restaurant_body'?'餐厅本体（来源类别支持）':branch.role==='auxiliary'?'非堂食附属点':'未知，待人工核实'} · {branch.source_category??'来源类别未取得'}</p></div>
   <div><span className={'branch-state '+branch.status}>{statuses[branch.status]}</span>
    <p>{branch.status==='skipped'?(reasons[branch.reason??'']??'未查询'):branch.status==='unavailable'?branch.error?.message??'来源不可用':'返回'+branch.returned_count+'家，纳入并集关联'+branch.hotel_ids.length+'家'}{branch.limited?' · 候选范围存在上限':''}</p>
    {branch.query_place&&branch.status!=='skipped'&&<details><summary>检索地点</summary><p>{branch.query_place}</p></details>}
    {(data.strategy==='map_hotels'||data.strategy==='named_hotel')&&<><p className="quiet">{data.strategy==='named_hotel'?'指定酒店地图检索：':'周边地点：'}{branch.nearby_status==='complete'?'已返回':branch.nearby_status==='unavailable'?'查询失败':'未查询'}</p>
     {branch.hotel_targets.length>0&&<details className="map-target-records"><summary>地图酒店与查名记录（{branch.hotel_targets.length}处）</summary>{branch.hotel_targets.map(target=><div className="map-target" key={target.id}><strong>{target.name}</strong><p>{target.address}</p><span>{target.status==='retained'&&target.match_status==='matched'?'同店字段一致，路线待核对':data.strategy==='named_hotel'&&target.status==='retained'&&target.reason==='source_city_unverified'?'酒店名址一致，来源城市未独立核实，待人工确认':targetStatus[target.status]}</span><p>{target.reason?reasons[target.reason]??'尚未核实':target.status==='retained'?'来源返回'+target.source_count+'条，需继续核对资格与路线':''}</p></div>)}</details>}</>}
    {(branch.status==='complete'||branch.status==='partial')&&<button type="button" className="text-button" aria-pressed={selected===branch.id} onClick={()=>onSelect(branch.id)}>查看该门店候选</button>}
   </div>
  </div>)}</div>
  {c.identical_branch_sets>0&&<p className="handoff-warning">{data.strategy==='map_hotels'?'多个门店地点关联相同酒店集合；可能来自重合的地图周边范围，仍须分别核对门店用途和实际路线。':'不同门店返回完全相同的住宿集合，供应商位置响应能力待核实；不能认定都在门店周边。'}</p>}
  {c.duplicate_hotels>0&&<p className="quiet">{c.duplicate_hotels}条重复住宿关联已合并，同名不同编号不合并。</p>}
  {c.identity_conflicts>0&&<p className="handoff-warning">{c.identity_conflicts}个住宿编号出现名称或地址冲突，已排除整项候选。</p>}
  <p className="quiet">门店检索只发送来源支持的日期、成人数和预算参考；儿童与房数保留在上下文，展开房型再发送完整组合。独卫、库存与完整费用仍未核实。</p>
 </section>;
}
