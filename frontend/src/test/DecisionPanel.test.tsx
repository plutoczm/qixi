import {render,screen} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import DecisionPanel,{PlanAssessmentCard} from '../DecisionPanel';
import type {DecisionWire,PlanAssessmentWire,ReturnTripConditionWire} from '../types';

const conditions:Array<ReturnTripConditionWire>=[
 {key:'stay_band',label:'住宿价格区间',verdict:'pass',basis:'inside',detail:'每房每晚在 100~200 元选入区间内。'},
 {key:'transport',label:'交通子指标',verdict:'unknown',basis:'missing',detail:'步行或驾车证据不完整。'},
 {key:'fare_gate',label:'返程车费',verdict:'fail',basis:'over_gate',detail:'车费上界超过 1500 分门槛。'},
];

const assessment=(planId:string,tier:PlanAssessmentWire['tier']):PlanAssessmentWire=>({
 plan_id:planId,tier,conditions,stay_known_fen:16900,fare_used_fen:null,total_known_fen:16900,
 guest_score_tenths:null,review_count:null,
 notes:['合计仅为住宿已知费用小计；另有 1 项未核实返程费用未计入。'],
 risks:['当前估价不等于未来深夜实价，也不构成锁价。','这是参考层结论，不代表可订、已核实库存或实付总价。'],
});

const decision=(over:Partial<DecisionWire>={}):DecisionWire=>({
 status:'not_comparable',order:[],excluded_ids:['p2'],reference_ids:['p1'],
 reasons:['排序主键为住客评分 → 评价人数，本轮没有同时取得这两项的方案。',
  '住客评分与评价人数当前来源均未提供；不按总成本回退排序，也不用酒店星级替代。'],
 assessments:[assessment('p1','reference_only'),assessment('p2','excluded')],
 ranking_basis:'住客评分 → 评价人数 → 已知费用小计 → 输入顺序',
 evidence_binding:'住宿金额与资格来自本次房型报价响应；返程路线、车费、夜间与评分证据不在本响应内，一律按未取得处理，不跨响应拼接、不读取已存价格。',
 data_mode:'live',...over,
});

const forbidden=['已核实可订','库存充足','实付总价已确认','锁价成功','逐夜达标','全网最低','多源比价','订单成功'];

describe('P7 decision panel',()=>{
 it('shows not-comparable without fabricating a ranking',()=>{
  const {container}=render(<DecisionPanel data={decision()}/>);
  expect(screen.getByText('本轮不可比较，未提供排名')).toBeInTheDocument();
  expect(screen.getByText(/排序主键：住客评分 → 评价人数/)).toBeInTheDocument();
  expect(screen.getByText(/不跨响应拼接/)).toBeInTheDocument();
  expect(container.querySelector('.decision-order')).toBeNull();
  expect(screen.getByText(/硬条件不通过：p2/)).toBeInTheDocument();
  expect(screen.getByText(/含未核实项、仅参考：p1/)).toBeInTheDocument();
 });

 it('renders all three verdicts and never prints a bookability promise',()=>{
  const {container}=render(<DecisionPanel data={decision()}/>);
  const text=container.textContent??'';
  expect(text).toContain('通过');
  expect(text).toContain('未核实');
  expect(text).toContain('不通过');
  expect(text).toContain('当前估价不等于未来深夜实价');
  for(const word of forbidden) expect(text).not.toContain(word);
 });

 it('marks synthetic fixtures explicitly',()=>{
  render(<DecisionPanel data={decision({data_mode:'test'})}/>);
  expect(screen.getByText(/测试数据 · 仅用于界面验证/)).toBeInTheDocument();
  expect(screen.getByText('合成夹具')).toBeInTheDocument();
 });

 it('orders by guest score then review count when both fields exist',()=>{
  const ranked=decision({
   status:'ranked',
   reasons:['排序主键：住客评分降序 → 评价人数降序 → 已知费用小计升序 → 输入顺序。'],
   excluded_ids:[],reference_ids:[],
   order:[
    {plan_id:'p2',position:1,guest_score_tenths:48,review_count:700,total_known_fen:18800,tie_break:'评分 → 评价人数 → 已知费用小计 → 输入顺序'},
    {plan_id:'p1',position:2,guest_score_tenths:46,review_count:900,total_known_fen:18100,tie_break:'评分 → 评价人数 → 已知费用小计 → 输入顺序'},
   ],
  });
  const {container}=render(<DecisionPanel data={ranked}/>);
  expect(screen.getByText('参考层排序结果')).toBeInTheDocument();
  expect(screen.getByText(/住客评分 4.8 · 评价人数 700/)).toBeInTheDocument();
  const items=Array.from(container.querySelectorAll('.decision-order li strong')).map(node=>node.textContent);
  expect(items).toEqual(['第1：p2','第2：p1']);
 });

 it('labels the known subtotal as a subtotal on a standalone assessment card',()=>{
  const {container}=render(<PlanAssessmentCard data={assessment('solo','reference_only')}/>);
  const text=container.textContent??'';
  expect(text).toContain('已知费用小计：¥169');
  expect(text).toContain('不是实付总价，不含未核实费用');
  expect(text).toContain('住客评分：未取得');
  for(const word of forbidden) expect(text).not.toContain(word);
 });
});
