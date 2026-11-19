import {render,screen,within} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import RoomQuoteRow from '../RoomQuoteRow';
import type {FeeBreakdownWire,Qualification,RoomQuote,SearchInput} from '../types';
import {quotesFixture} from './fixtures';

/** Re-price the fixture breakdown so the panel and the price block agree. */
function reprice(fees:FeeBreakdownWire,amount:number|null):FeeBreakdownWire {
  if (amount===null) {
    return {...fees,room_charge_fen:null,total_known_fen:null,per_room_night_fen:null,lines:[]};
  }
  return {
    ...fees,
    room_charge_fen:amount,
    total_known_fen:amount,
    per_room_night_fen:amount,
    lines:fees.lines.map(line=>line.kind==='room_charge'
      ?{...line,amount_fen:amount,total_fen:amount,note:`来源为每间每晚价：${amount} 分 × 1 晚 × 1 间。`}
      :line),
  };
}

function show(override:Partial<RoomQuote>={},contextOverride:Partial<SearchInput>={},qualificationOverride:Partial<Qualification>={}) {
  const fixture=quotesFixture();
  const base=fixture.quotes[0];
  const quote:RoomQuote={...base,...override};
  const baseQualification=quote.qualification;
  if (baseQualification) {
    quote.qualification={
      ...baseQualification,
      fees:quote.amount_fen===base.amount_fen?baseQualification.fees:reprice(baseQualification.fees,quote.amount_fen),
      ...qualificationOverride,
    };
  }
  const context={...fixture.context,budget_fen:20000,...contextOverride};
  render(<RoomQuoteRow quote={quote} context={context} index={0}/>);
}

/** The price block, so a fee line with the same number cannot satisfy an assertion. */
function priceBlock():HTMLElement {
  const node=document.querySelector('.quote-price');
  if (!node) throw new Error('price block missing');
  return node as HTMLElement;
}

describe('rate-plan evidence is never advertised as a bookable room',()=>{
  it('separates reference price, source occupancy, non-on-request and unknown final conditions',()=>{
    show({amount_fen:17200,currency:'CNY',max_occupancy:4,on_request:false});
    expect(screen.getByRole('group',{name:'测试大床房 · 报价方案1'})).toBeInTheDocument();
    expect(within(priceBlock()).getByText('¥172')).toBeInTheDocument();
    expect(screen.getByText('道旅展示均价 · 非最终总价')).toBeInTheDocument();
    expect(screen.getByText('来源标称单间最多4人；仅供2成人容量初筛，实际入住资格未核实。')).toBeInTheDocument();
    expect(screen.getByText('来源未标记需申请，不代表库存已确认')).toBeInTheDocument();
    expect(screen.getByText('参考层：整间与独卫按国内默认推定（未取得来源字段），展示价按已含税处理；到店另付未核实，且无来源跳转，非可订实付价。')).toBeInTheDocument();
    expect(screen.queryByText(/已确认可订|库存充足|立即预订|合格最低价/)).toBeNull();
  });
  it('prints on-request=true as a requirement, not guaranteed availability',()=>{
    show({on_request:true,max_occupancy:2});
    expect(screen.getByText('来源标记：需二次确认')).toBeInTheDocument();
    expect(screen.getByText(/非可订实付价/)).toBeInTheDocument();
  });
  it('preserves unknown request state, capacity and price instead of guessing',()=>{
    show({on_request:null,max_occupancy:null,amount_fen:null});
    expect(screen.getByText('来源未提供需申请状态，不能推断有房')).toBeInTheDocument();
    expect(screen.getByText('来源未提供单间最多入住人数；容量待核实。')).toBeInTheDocument();
    expect(within(priceBlock()).getByText('参考价待核实')).toBeInTheDocument();
    // A missing amount must not be dressed up as a budget pass.
    expect(screen.getByText('预算无法判定 · 金额缺失（详见费用明细）')).toBeInTheDocument();
    expect(screen.queryByText('¥0')).toBeNull();
  });
  it('flags a one-room, two-adult source capacity below two without discarding the quoted price',()=>{
    show({max_occupancy:1,amount_fen:16900});
    expect(screen.getByText(/来源标称单间最多1人，少于本次2成人；人数容量初筛不通过/)).toBeInTheDocument();
    expect(within(priceBlock()).getByText('¥169')).toBeInTheDocument();
  });
  it('does not call a four-person two-room arrangement short on the strength of per-room capacity alone',()=>{
    show({max_occupancy:2},{rooms:2,adults:4});
    expect(screen.getByText('来源标称单间最多2人；本次2间房的成人及儿童分配需另行核实。')).toBeInTheDocument();
    expect(screen.queryByText(/人数容量初筛不通过/)).toBeNull();
  });
  it('keeps child allocation unknown even with source max occupancy above the adult count',()=>{
    show({max_occupancy:4},{children_ages:[6]});
    expect(screen.getByText('来源标称单间最多4人；本次1间房的成人及儿童分配需另行核实。')).toBeInTheDocument();
  });
  it('does not infer private bathroom from the hotel name even when it claims an ensuite',()=>{
    show({room_name:'独卫商务大床房',on_request:false});
    // A name that says "独卫" is only *consistent* with the domestic default;
    // it must never be rendered as verified evidence.
    const notes=screen.getAllByText(/参考层：整间与独卫按国内默认推定/);
    expect(notes.every(note=>!note.textContent?.includes('已核实'))).toBe(true);
  });
  it('distinguishes a known-cost budget failure from a final payable total',()=>{
    show({amount_fen:20900},{},{
      budget:{
        verdict:'fail',basis:'single_night_known_costs',
        detail:'已知费用折算每房每晚 20900 分 > 预算 20000 分。',
        budget_fen:20000,compared_fen:20900,
      },
    });
    const note=screen.getByText('预算不通过 · 单晚已知费用口径（详见费用明细）');
    expect(note).toHaveClass('over-budget');
    expect(within(priceBlock()).getByText('¥209')).toBeInTheDocument();
    expect(screen.getByText(/已知费用折算每房每晚 20900 分 > 预算 20000 分。/)).toBeInTheDocument();
  });
  it('never claims a budget pass when no budget was requested',()=>{
    show({amount_fen:17900},{budget_fen:null});
    expect(screen.getByText('未设置每房每晚预算；本次合格判定不含预算门槛（详见费用明细）')).toBeInTheDocument();
    expect(screen.queryByText(/预算通过/)).toBeNull();
  });
  it('does not label a non-CNY source price as yuan or compare it with the yuan budget',()=>{
    // The server drops the amount when the currency is not CNY, so the engine
    // sees no amount and the panel must stay unknown.
    show({amount_fen:null,currency:'USD'});
    expect(within(priceBlock()).getByText('参考价待核实')).toBeInTheDocument();
    expect(screen.getByText('预算无法判定 · 金额缺失（详见费用明细）')).toBeInTheDocument();
    expect(screen.queryByText('¥159')).toBeNull();
  });
  it('keeps original cancellation policy visible without treating it as stock evidence',()=>{
    show();
    expect(screen.getByText(/不可退 ·/)).toBeInTheDocument();
    // Shown twice on purpose: once as the rate plan's own terms, once as the
    // evidence behind the cancellation condition.
    expect(screen.getAllByText(/预订后不可退款/).length).toBe(2);
  });
});

describe('P6 qualification and fee breakdown',()=>{
  it('marks a quote with an unknown condition as reference, never as qualified',()=>{
    show();
    expect(screen.getByText('仅参考（有未核实项）')).toBeInTheDocument();
    expect(screen.getByText('没有判定失败项，但仍有未核实项，只能作为参考价展示。')).toBeInTheDocument();
    expect(screen.getByText('未核实项：足量库存：来源协议没有剩余房量字段，不能作为有房依据。')).toBeInTheDocument();
    expect(screen.queryByText('合格（含推定标注）')).toBeNull();
  });
  it('labels a presumed pass as presumed and says it cannot support a booking claim',()=>{
    show();
    expect(screen.getAllByText('通过（推定）')).toHaveLength(2);
    expect(screen.getByText('2项按已批准默认推定通过：整间客房、房内独立卫浴。推定项不足以支撑“可订”。')).toBeInTheDocument();
    // A plain pass must not be dressed up as a presumption.
    expect(screen.getAllByText('通过').length).toBeGreaterThan(0);
  });
  it('renders the qualified tier with its own wording, still not a booking',()=>{
    show({}, {}, {tier:'qualified',reasons:[]});
    expect(screen.getByText('合格（含推定标注）')).toBeInTheDocument();
    expect(screen.getByText('资格与预算均未发现问题；代表可展示的合格参考价，不代表可订。')).toBeInTheDocument();
  });
  it('shows an excluded quote with the failing conditions and reasons',()=>{
    show({},{},{
      tier:'excluded',
      reasons:['房内独立卫浴：房型标注床位／多人间，不推定独卫。'],
    });
    expect(screen.getByText('已排除')).toBeInTheDocument();
    expect(screen.getByText('判定失败：房内独立卫浴：房型标注床位／多人间，不推定独卫。')).toBeInTheDocument();
  });
  it('keeps counted, not-counted and unknown money apart',()=>{
    show();
    expect(screen.getByText('已知费用小计：')).toBeInTheDocument();
    expect(screen.getByText('1晚 × 1间 · 来源声明展示价含税 · 不含下列未核实项')).toBeInTheDocument();
    expect(screen.getByText('未计入合计（1项，不当 0 也不当已付）：')).toBeInTheDocument();
    expect(screen.getByText('税费是否已含未核实')).toBeInTheDocument();
    // An unverified cost is never silently folded into the subtotal.
    expect(screen.queryByText('¥0')).toBeNull();
  });
  it('prints the budget basis verbatim so an averaged pass cannot read as every-night verified',()=>{
    show({},{},{
      budget:{
        verdict:'pass',basis:'average_presumed',
        detail:'均价口径通过：已知费用折算每房每晚 18800 分 ≤ 预算 20000 分；未取得逐夜价，无法排除其中一晚超预算。',
        budget_fen:20000,compared_fen:18800,
      },
    });
    expect(screen.getByText('预算通过 · 均价口径（未验证逐夜）（详见费用明细）')).toBeInTheDocument();
    expect(screen.getByText(/均价口径通过：已知费用折算每房每晚 18800 分 ≤ 预算 20000 分；未取得逐夜价，无法排除其中一晚超预算。/)).toBeInTheDocument();
    expect(screen.queryByText('逐夜价已核实口径')).toBeNull();
  });
  it('reports an unevaluated quote instead of inventing a verdict',()=>{
    show({qualification:null});
    expect(screen.getByText('本报价未评估资格与费用（服务端未返回结论），因此不显示合格或参考标记。')).toBeInTheDocument();
    expect(screen.queryByText('合格（含推定标注）')).toBeNull();
    expect(screen.queryByText('仅参考（有未核实项）')).toBeNull();
    expect(screen.queryByText('已排除')).toBeNull();
  });
});

/** r72: real Tuniu data has no fee rows at all (unknown_items + aside both empty). */
describe('fee panel when the source returns no fee rows',()=>{
  it('does not point at a list that does not exist, and still names the risk',()=>{
    const base=quotesFixture().quotes[0].qualification!;
    const empty:FeeBreakdownWire={
      ...base.fees,
      unknown_items:[],
      lines:base.fees.lines.filter(line=>line.kind==='room_charge'),
    };
    show({}, {}, {fees:empty});
    expect(screen.queryByText(/不含下列未核实项/)).not.toBeInTheDocument();
    expect(screen.getByText(/未列出未核实费用项，到店另付见报价说明/)).toBeInTheDocument();
    expect(screen.queryByText(/未计入合计（/)).not.toBeInTheDocument();
  });
});
