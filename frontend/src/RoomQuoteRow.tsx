import {money} from './format';
import type {BudgetAssessmentWire, Qualification, RoomQuote, SearchInput} from './types';

function capacityNote(quote:RoomQuote, context:SearchInput):string {
  if (quote.max_occupancy===null) return '来源未提供单间最多入住人数；容量待核实。';
  if (context.rooms===1 && context.children_ages.length===0 && quote.max_occupancy<context.adults) {
    return `来源标称单间最多${quote.max_occupancy}人，少于本次${context.adults}成人；人数容量初筛不通过。`;
  }
  if (context.rooms===1 && context.children_ages.length===0) {
    return `来源标称单间最多${quote.max_occupancy}人；仅供${context.adults}成人容量初筛，实际入住资格未核实。`;
  }
  return `来源标称单间最多${quote.max_occupancy}人；本次${context.rooms}间房的成人及儿童分配需另行核实。`;
}

function requestNote(value:RoomQuote['on_request']):string {
  if (value===true) return '来源标记：需二次确认';
  if (value===false) return '来源未标记需申请，不代表库存已确认';
  return '来源未提供需申请状态，不能推断有房';
}

function budgetNote(quote:RoomQuote, context:SearchInput):string {
  if (quote.amount_fen===null || !Number.isSafeInteger(quote.amount_fen) || quote.amount_fen<0 || quote.currency!=='CNY') return '展示参考价待核实，无法比较人民币预算。';
  if (context.budget_fen===null) return '未设置每房每晚参考预算；仍需核实最终含费价。';
  if (quote.amount_fen>context.budget_fen) {
    return `展示参考价已高于¥${money(context.budget_fen)}预算；不是最终费用核算。`;
  }
  return `展示参考价未超过¥${money(context.budget_fen)}预算；不代表最终含费价达标。`;
}

/**
 * Reference-layer wording for one quote. A presumption is always spelled out as
 * a presumption: the row must never read as if the room or its bathroom were
 * verified, and a blocked quote must not be shown as a reference price.
 */
function referenceNote(quote:RoomQuote):string {
  const gate=quote.reference;
  if (!gate) return '本报价未评估参考层资格，因此不展示为参考价。';
  if (!gate.ready) {
    const blocked=gate.items.filter(item=>item.status==='blocked').map(item=>item.label);
    return `参考层不通过：${blocked.join('、')}；该报价不展示为参考价。`;
  }
  // A bed place the user accepted is relaxed by their own choice, not by the
  // domestic default: saying "按国内默认推定" here would describe a different
  // rule than the one that actually let this row through.
  const accepted=gate.items
    .filter(item=>item.status==='accepted')
    .map(item=>item.label);
  const roomTerms=accepted.length>0
    ?`整间与独卫已按你接受床位放宽（${accepted.join('、')}，非来源核实）`
    :'整间与独卫按国内默认推定（未取得来源字段）';
  return `参考层：${roomTerms}，展示价按已含税处理；到店另付未核实，且无来源跳转，非可订实付价。`;
}

/** Title of the three-state verdict. Never a bare "OK/可订" badge. */
export const tierText:Record<Qualification['tier'],string>={
  qualified:'合格（含推定标注）',
  reference:'仅参考（有未核实项）',
  excluded:'已排除',
};

const tierCaption:Record<Qualification['tier'],string>={
  qualified:'资格与预算均未发现问题；代表可展示的合格参考价，不代表可订。',
  reference:'没有判定失败项，但仍有未核实项，只能作为参考价展示。',
  excluded:'有判定失败项，不展示为参考价。',
};

/** How the money unit was expanded into a stay total. */
const periodText:Record<string,string>={
  stay:'整单',room_night:'每间每晚',room_stay:'每间每单',person_night:'每人每晚',
};

const taxBasisText:Record<string,string>={
  source_states_tax_included:'来源声明展示价含税',
  source_states_tax_excluded:'来源声明不含税，税额未提供',
  domestic_default_tax_included:'来源未注明含税，按境内默认推定含税（非来源确认）',
  tax_treatment_unknown:'税费是否已含未核实',
};

/** The basis matters: only `nightly_verified` proves every single night fits. */
export const budgetBasisText:Record<BudgetAssessmentWire['basis'],string>={
  no_budget:'未设置预算门槛',
  single_night_known_costs:'单晚已知费用口径',
  nightly_verified:'逐夜价已核实口径',
  average_presumed:'均价口径（未验证逐夜）',
  nightly_incomplete:'逐夜价不完整（无法逐晚校验）',
  total_unknown:'金额缺失，无法比较预算',
};

/**
 * Fee breakdown for one quote, every amount an integer fen.
 *
 * Three groups are kept apart on purpose: what is counted into the known
 * subtotal, what is listed but not counted (refundable deposit, unverified
 * fee, unverified discount), and what is simply unknown. Merging them is how
 * an "all fees included" claim gets invented.
 */
export function FeePanel({qualification}:{qualification:Qualification}) {
  const fees=qualification.fees;
  const counted=fees.lines.filter(line=>line.counted&&line.kind!=='discount');
  const aside=fees.lines.filter(line=>!line.counted&&line.kind!=='room_charge');
  const applied=fees.lines.filter(line=>line.kind==='discount'&&line.counted);
  return <div className="fee-panel">
    <ul className="fee-lines">
      {counted.map(line=><li key={line.kind+line.label}>
        <span>{line.label}</span>
        <strong>¥{money(line.total_fen)}</strong>
        <small>{periodText[line.per]??line.per} · {line.note}</small>
      </li>)}
      {applied.map(line=><li key={'d'+line.label} className="fee-discount">
        <span>{line.label}（已核实）</span>
        <strong>-¥{money(line.total_fen)}</strong>
        <small>{line.note}</small>
      </li>)}
    </ul>
    <p className="fee-subtotal">
       <span>已知费用小计：</span><strong>{fees.total_known_fen===null?'待核实':'¥'+money(fees.total_known_fen)}</strong>
      {fees.per_room_night_fen!==null&&<>（每房每晚 ¥{money(fees.per_room_night_fen)}）</>}
      {/* r72: on real Tuniu data the source returns no fee rows at all, so both
          `unknown_items` and `aside` are empty. Printing "不含下列未核实项"
          there leaves a dangling "下列" with nothing below it, and reads as if
          the absence of rows meant there are no extra costs. Say so instead. */}
      <small>{`${fees.nights}晚 × ${fees.rooms}间 · ${taxBasisText[fees.tax_basis]??fees.tax_basis} · ${
        fees.unknown_items.length>0||aside.length>0
          ?'不含下列未核实项'
          :'未列出未核实费用项，到店另付见报价说明'}`}</small>
    </p>
    {aside.length>0&&<ul className="fee-aside">
      {aside.map(line=><li key={line.kind+line.label}>
        <span>{line.label}</span><strong>¥{money(line.total_fen)}</strong><small>{line.note}</small>
      </li>)}
    </ul>}
    {fees.unknown_items.length>0&&<div className="fee-unknown">
      <strong>未计入合计（{fees.unknown_items.length}项，不当 0 也不当已付）：</strong>
      <ul>{fees.unknown_items.map(item=><li key={item}>{item}</li>)}</ul>
    </div>}
    <p className={'fee-budget budget-'+qualification.budget.verdict}>
      预算判定：{budgetBasisText[qualification.budget.basis]??qualification.budget.basis} · {qualification.budget.detail}
    </p>
  </div>;
}

/**
 * P6 qualification for one quote. A `qualified` tier means "no condition or
 * budget problem was found" -- it is deliberately not the word 可订, because
 * the booking layer rejects every presumption this tier allows.
 */
export function QualificationPanel({qualification}:{qualification:Qualification}) {
  const presumed=qualification.conditions.filter(row=>row.presumed);
  const accepted=qualification.conditions.filter(row=>row.accepted);
  const notPassed=qualification.conditions.filter(row=>row.verdict!=='pass');
  return <div className={'qualification tier-'+qualification.tier}>
    <div className="tier-heading">
      <strong>{tierText[qualification.tier]}</strong>
      <span>{tierCaption[qualification.tier]}</span>
    </div>
    <ul className="tier-conditions">
      {qualification.conditions.map(row=><li key={row.condition} className={'verdict-'+row.verdict}>
        <span>{row.label}</span>
        <em>{row.verdict==='pass'?(row.accepted?'接受后通过':row.presumed?'通过（推定）':'通过'):row.verdict==='fail'?'不通过':'未核实'}</em>
        <small>{row.detail}</small>
      </li>)}
    </ul>
    {presumed.length>0&&<p className="tier-presumed">
      {presumed.length}项按已批准默认推定通过：{presumed.map(row=>row.label).join('、')}。推定项不足以支撑“可订”。
    </p>}
    {accepted.length>0&&<p className="tier-presumed">
      {accepted.length}项因你接受床位而放宽：{accepted.map(row=>row.label).join('、')}。这不是来源核实，也不是我们的推定，是你在本次查询里作出的选择。
    </p>}
    {notPassed.length>0&&<p className="tier-reasons">
      {qualification.tier==='excluded'?'判定失败：':'未核实项：'}
      {/* Each reason already ends with 。; join without doubling the punctuation. */}
      {qualification.reasons.length>0
        ?qualification.reasons.map(reason=>reason.replace(/。$/,'')).join('；')+'。'
        :notPassed.map(row=>row.label).join('、')+'。'}
    </p>}
    <FeePanel qualification={qualification}/>
  </div>;
}

/** One-line budget state. The full reason lives in the fee panel below. */
function budgetShort(qualification:Qualification):string {
  const budget=qualification.budget;
  // A missing amount cannot pass or fail a budget, whatever the verdict says.
  if (qualification.fees.total_known_fen===null) return '预算无法判定 · 金额缺失（详见费用明细）';
  if (budget.basis==='no_budget') return '未设置每房每晚预算；本次合格判定不含预算门槛（详见费用明细）';
  const verdict=budget.verdict==='pass'?'通过':budget.verdict==='fail'?'不通过':'无法判定';
  return `预算${verdict} · ${budgetBasisText[budget.basis]??budget.basis}（详见费用明细）`;
}

/** Evidence-first rendering of a single supplier rate plan, never a bookable room. */
export default function RoomQuoteRow({quote,context,index,sourceName='道旅'}:{quote:RoomQuote;context:SearchInput;index:number;sourceName?:string}) {
  const hasCnyReference=quote.amount_fen!==null && Number.isSafeInteger(quote.amount_fen) && quote.amount_fen>=0 && quote.currency==='CNY';
  const requestStatus=quote.on_request===true?'needs-request':quote.on_request===false?'not-marked':'unknown';
  const overBudget=context.budget_fen!==null && hasCnyReference && quote.amount_fen!==null && quote.amount_fen>context.budget_fen;
  // A bed place is priced per person. Showing that number under the usual
  // "房型参考价" caption would read as one room for ¥60, so the caption has to
  // say which unit the number is in.
  const perPerson=quote.qualification?.fees.lines
    .some(line=>line.kind==='room_charge'&&line.per==='person_night')===true;
  return <div className="quote-row" role="group" aria-label={`${quote.room_name} · 报价方案${index+1}`}>
    <div className="quote-description">
      <h5>{quote.room_name}</h5>
      {quote.plan_name && quote.plan_name!==quote.room_name && <p className="plan-name">{quote.plan_name}</p>}
      <p className="room-facts">{[
        quote.bed_type,quote.meal,
        quote.room_size_sqm ? quote.room_size_sqm+'㎡' : null,
      ].filter(Boolean).join(' · ') || '床型与房间设施待核实'}</p>
      <p className={'room-capacity-note'+(context.rooms===1 && context.children_ages.length===0 && quote.max_occupancy!==null && quote.max_occupancy<context.adults?' capacity-short':'')}>
        {capacityNote(quote,context)}
      </p>
      <p className={'cancel-policy'+(quote.cancellation.can_cancel===false?' nonrefundable':'')}>
        {quote.cancellation.can_cancel===false && <strong>不可退 · </strong>}{quote.cancellation.text}
      </p>
      <span className={'room-request-status '+requestStatus}>{requestNote(quote.on_request)}</span>
      {quote.source_inventory_observed!==null && <p className="room-unverified">来源本次库存观察：{quote.source_inventory_observed}；未核实整住期、人数与最终可订资格，不代表已锁房。</p>}
      <p className={'room-unverified'+(quote.reference&&!quote.reference.ready?' reference-blocked':'')}>
        {referenceNote(quote)}
      </p>
    </div>
    <div className="quote-price">
      <span>{hasCnyReference?'¥'+money(quote.amount_fen):'参考价待核实'}</span>
      <small>{quote.reference&&!quote.reference.ready
        ?`${sourceName}报价 · 参考层未通过，不展示为参考价`
        :perPerson
          ?`${sourceName}床位单价 · 每人每晚 · 非最终总价`
          :`${sourceName}${quote.price_basis==='supplier_reference'?'房型参考价':'展示均价'} · 非最终总价`}</small>
      <small className={'room-budget-note'+(overBudget?' over-budget':'')}>
        {quote.qualification?budgetShort(quote.qualification):budgetNote(quote,context)}
      </small>
    </div>
    {quote.qualification
      ?<QualificationPanel qualification={quote.qualification}/>
      :<p className="tier-missing">本报价未评估资格与费用（服务端未返回结论），因此不显示合格或参考标记。</p>}
  </div>;
}
