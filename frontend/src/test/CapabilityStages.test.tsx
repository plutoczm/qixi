import {render,screen,within} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import {assessBookabilityEvidence,assessCandidateEvidence,BookabilityGate,CapabilityStages} from '../CapabilityStages';
import {quotesFixture,searchFixture} from './fixtures';

describe('two independent product capabilities',()=>{
  it('only labels a live returned hotel as a browsable source candidate',()=>{
    const result=searchFixture();
    result.data_mode='live';
    const evidence=assessCandidateEvidence(result);
    expect(evidence.state).toBe('reference');
    expect(evidence.title).toBe('已取得来源候选');
    render(<CapabilityStages result={result}/>);
    const region=screen.getByRole('region',{name:'两层产品能力与状态'});
    expect(within(region).getByText('第一层 · 智能找酒店')).toBeInTheDocument();
    expect(within(region).getByText('已取得来源候选')).toBeInTheDocument();
    expect(within(region).getByText('第二层 · 可订房型验证')).toBeInTheDocument();
    expect(within(region).getByText('尚未证明符合全部预订条件')).toBeInTheDocument();
    expect(within(region).queryByText(/已核实可订|库存已确认|全部条件通过/)).toBeNull();
  });

  it('never turns simulated results into production evidence',()=>{
    const result=searchFixture();
    expect(assessCandidateEvidence(result).state).toBe('test');
    render(<CapabilityStages result={result}/>);
    expect(screen.getByText('模拟候选，仅供测试')).toBeInTheDocument();
    expect(screen.getByText(/本次并非真实来源/)).toBeInTheDocument();
  });

  it('distinguishes empty bounded search from an unavailable supplier',()=>{
    const empty=searchFixture();
    empty.data_mode='live';
    empty.hotels=[];
    expect(assessCandidateEvidence(empty).state).toBe('empty');
    expect(assessCandidateEvidence(empty).detail).toContain('整个城市');
    const failed={...empty,status:'unavailable' as const};
    expect(assessCandidateEvidence(failed).state).toBe('unavailable');
    expect(assessCandidateEvidence(failed).detail).toContain('来源错误');
  });

  it('shows booking uncertainty in plain language before optional detailed evidence',()=>{
    render(<BookabilityGate data={quotesFixture()}/>);
    expect(screen.getByText('预订前，还需要确认这些信息')).toBeInTheDocument();
    expect(screen.getByText(/暂不能视为已可预订/)).toBeInTheDocument();
    const details=screen.getByText('查看详细核验情况').closest('details');
    expect(details).not.toHaveAttribute('open');
  });

  it('matching URL and matching occupancy cannot elevate unknown room/privacy/fees/inventory',()=>{
    const details=quotesFixture();
    details.data_mode='live';
    details.booking_status='matched';
    details.occupancy_status='matched';
    details.quotes[0].on_request=false;
    details.quotes[0].max_occupancy=4;
    details.quotes[0].amount_fen=9800;
    const evidence=assessBookabilityEvidence(details);
    expect(evidence.status).toBe('unverified');
    expect(evidence.evaluated).toBe(true);
    // The missing list is the server's blocked rows, not a hardcoded paragraph.
    expect(evidence.missing).toEqual(['足量库存','押金与到店必付','入住组合回显']);
    render(<BookabilityGate data={details}/>);
    const region=screen.getByRole('region',{name:'房型可订资格状态'});
    expect(within(region).getByText('第二层 · 可订资格：尚未核实通过')).toBeInTheDocument();
    expect(region).toHaveTextContent('来源链接参数已核对（不是已确认可订）');
    expect(region).toHaveTextContent('供应商人数与房数回显一致（仍不证明库存）');
    expect(region).toHaveTextContent('不代表房间库存或已可预订');
    expect(within(region).queryByRole('link')).toBeNull();
  });

  it('shows a server-verified link as verified without claiming bookable',()=>{
    const details=quotesFixture();
    details.data_mode='live';
    render(<BookabilityGate data={details}/>);
    const region=screen.getByRole('region',{name:'房型可订资格状态'});
    // The verified row is still listed as a blocker-free fact, not as a pass.
    expect(region).toHaveTextContent('第二层 · 可订资格：尚未核实通过');
    expect(region).not.toHaveTextContent('来源预订链接');
    expect(region).toHaveTextContent('押金与到店必付');
  });

  it('treats a missing booking verdict as not evaluated, never as a pass',()=>{
    const details=quotesFixture();
    details.bookability=null;
    const evidence=assessBookabilityEvidence(details);
    expect(evidence.status).toBe('unverified');
    expect(evidence.evaluated).toBe(false);
    expect(evidence.missing.join('')).toContain('未评估');
    render(<BookabilityGate data={details}/>);
    const region=screen.getByRole('region',{name:'房型可订资格状态'});
    expect(region).toHaveTextContent('可订层本次未评估');
    expect(region).not.toHaveTextContent('来源已逐项核实通过');
  });

  it('only says the booking layer passed when the server gate says ready',()=>{
    const details=quotesFixture();
    details.bookability={ready:true,items:[
      {field:'inventory_quantity',label:'足量库存',status:'verified',note:'来源自称剩余房量。'},
      {field:'occupancy_echo',label:'入住组合回显',status:'verified',note:'来源已回显本次人数与房数。'},
    ]};
    const evidence=assessBookabilityEvidence(details);
    expect(evidence.status).toBe('ready');
    expect(evidence.missing).toEqual([]);
    render(<BookabilityGate data={details}/>);
    expect(screen.getByText(/可订层门禁判定通过/)).toBeInTheDocument();
  });

  it.each([
    ['mismatch','mismatch','供应商人数或房数回显冲突','来源链接条件冲突，已停用'],
    ['unverified','unverified','供应商人数与房数回显未完整核实','来源链接条件未完整核实'],
    ['matched','unavailable','供应商人数与房数回显一致','来源链接不可用'],
  ] as const)('never treats %s occupancy and %s link as bookable',(
    occupancy,booking,expectOccupancy,expectLink,
  )=>{
    const details=quotesFixture();
    details.occupancy_status=occupancy;
    details.booking_status=booking;
    const evidence=assessBookabilityEvidence(details);
    expect(evidence.status).toBe('unverified');
    expect(evidence.sourceContext).toContain(expectOccupancy);
    expect(evidence.sourceLink).toContain(expectLink);
  });

  it('empty source quote list still cannot claim zero inventory or zero charge',()=>{
    const details=quotesFixture();
    details.quotes=[];
    render(<BookabilityGate data={details}/>);
    expect(screen.getByText(/当前有0条来源报价方案/)).toBeInTheDocument();
    expect(screen.getByText(/不能直接认定可订/)).toBeInTheDocument();
    expect(screen.queryByText(/没有房间|房价免费|已核实可订/)).toBeNull();
  });
});
