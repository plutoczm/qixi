import type {DecisionWire,PlanAssessmentWire,ReturnTripConditionWire} from './types';
import {money} from './format';

const verdictLabel:Record<string,string>={pass:'通过',fail:'不通过',unknown:'未核实'};
const tierLabel:Record<string,string>={rankable:'可参与排序',reference_only:'仅参考展示',excluded:'已排除'};
const amount=(value:number|null)=>value===null?'未取得':'¥'+money(value);
const score=(value:number|null)=>value===null?'未取得':`${Math.floor(value/10)}.${value%10}`;

function ConditionRows({conditions}:{conditions:Array<ReturnTripConditionWire>}) {
  return <table className="decision-conditions">
    <thead><tr><th scope="col">条件</th><th scope="col">判定</th><th scope="col">依据</th></tr></thead>
    <tbody>{conditions.map(row=><tr key={row.key} className={'decision-'+row.verdict}>
      <th scope="row">{row.label}</th><td>{verdictLabel[row.verdict]??'未核实'}</td><td>{row.detail}</td>
    </tr>)}</tbody>
  </table>;
}

export function PlanAssessmentCard({data}:{data:PlanAssessmentWire}) {
  return <article className={'plan-assessment tier-'+data.tier} aria-label={'方案 '+data.plan_id+' 的返程判定'}>
    <div className="plan-assessment-head"><strong>方案 {data.plan_id}</strong><span className={'tier-badge '+data.tier}>{tierLabel[data.tier]}</span></div>
    <ConditionRows conditions={data.conditions}/>
    <div className="decision-amounts">
      <p>住宿已知费用小计：{amount(data.stay_known_fen)} · 本次返程计入：{amount(data.fare_used_fen)}</p>
      <p><strong>已知费用小计：{amount(data.total_known_fen)}</strong>（不是实付总价，不含未核实费用）</p>
      <p>住客评分：{score(data.guest_score_tenths)} · 评价人数：{data.review_count===null?'未取得':data.review_count}</p>
    </div>
    {data.notes.length>0&&<ul className="decision-notes">{data.notes.map((note,index)=><li key={index}>{note}</li>)}</ul>}
    {data.risks.length>0&&<ul className="decision-risks">{data.risks.map((risk,index)=><li key={index}>{risk}</li>)}</ul>}
  </article>;
}

export default function DecisionPanel({data}:{data:DecisionWire}) {
  return <section className="decision-panel" aria-label="返程与综合成本参考">
    <div className="decision-heading"><div><h4>返程与综合成本参考</h4><p>先硬筛，再排序；未知不冒充通过。</p></div><span className="source-label">{data.data_mode==='test'?'合成夹具':'本次响应'}</span></div>
    {data.data_mode==='test'&&<p className="test-banner">测试数据 · 仅用于界面验证，不代表真实来源</p>}
    <p className="decision-basis">排序主键：{data.ranking_basis}</p>
    <p className="decision-binding">{data.evidence_binding}</p>
    <div className={'decision-status '+(data.status==='ranked'?'ranked':'not-comparable')} role="status">
      <strong>{data.status==='ranked'?'参考层排序结果':'本轮不可比较，未提供排名'}</strong>
      <ul>{data.reasons.map((reason,index)=><li key={index}>{reason}</li>)}</ul>
    </div>
    {data.order.length>0&&<div className="decision-order"><h5>排序（参考层，不代表可订）</h5><ol>{data.order.map(row=><li key={row.plan_id}>
      <strong>第{row.position}：{row.plan_id}</strong>
      <span>住客评分 {score(row.guest_score_tenths)} · 评价人数 {row.review_count} · 已知费用小计 {amount(row.total_known_fen)}</span>
      <small>{row.tie_break}</small>
    </li>)}</ol></div>}
    {data.excluded_ids.length>0&&<p className="decision-ids">硬条件不通过：{data.excluded_ids.join('、')}</p>}
    {data.reference_ids.length>0&&<p className="decision-ids">含未核实项、仅参考：{data.reference_ids.join('、')}</p>}
    <div className="decision-plans">{data.assessments.map(row=><PlanAssessmentCard key={row.plan_id} data={row}/>)}</div>
  </section>;
}
