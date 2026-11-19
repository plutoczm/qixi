import {money} from './format';
import type {GateItem, QuotesResponse} from './types';

const contextFields:Record<string,string>={
  checkInDate:'入住日期',checkOutDate:'退房日期',adultCount:'成人数',
  childCount:'儿童数',childAgeDetails:'儿童年龄',roomCount:'房间数',
  hotelId:'酒店编号',link:'来源链接',
};
const issueTypes:Record<string,string>={
  missing:'缺少回显',mismatch:'与本次条件不一致',
  unparseable:'无法核验格式',unknown:'无法确认',
  absent:'缺少或不安全',
};
/** Status wording is fixed: a presumption must never read as a verification. */
const statusText:Record<GateItem['status'],string>={
  verified:'已核实',
  presumed:'推定',
  accepted:'你已接受',
  unknown:'未核实',
  unsupported:'来源不提供',
  blocked:'不满足',
};

function safeIssueLabels(issues:string[]):string {
  return issues.slice(0,12).map(code=>{
    if(code==='link_absent_or_unsafe')return '来源链接缺少或不符合安全域名规则';
    if(code==='link_context_unknown')return '来源链接参数无法核验';
    const mark=code.lastIndexOf('_');
    const field=code.slice(0,mark),state=code.slice(mark+1);
    return contextFields[field]&&issueTypes[state]
      ?contextFields[field]+issueTypes[state]:'其他上下文字段待核实';
  }).join('；');
}

/**
 * Reference-layer evidence for a quotes response.
 *
 * The layer is evaluated per quote on the server, because room form and
 * remaining inventory differ per quote. This panel therefore shows the gate of
 * the cheapest returned quote as the representative row, and states how many of
 * the returned quotes are displayable at all. A `ready` gate never means the
 * room is bookable: the booking layer rejects every presumption.
 */
export default function QuoteEvidence({data}:{data:QuotesResponse}) {
  const nights=(Date.parse(data.context.check_out)-Date.parse(data.context.check_in))/86400000;
  const onRequest=data.quotes.filter(quote=>quote.on_request===true).length;
  const notMarked=data.quotes.filter(quote=>quote.on_request===false).length;
  const requestUnknown=data.quotes.filter(quote=>quote.on_request===null).length;
  const capacityUnknown=data.quotes.filter(quote=>quote.max_occupancy===null).length;
  const evaluated=data.quotes.filter(quote=>quote.reference!==null).length;
  const displayable=data.quotes.filter(quote=>quote.reference?.ready===true).length;
  const representative=data.quotes.find(quote=>quote.reference!==null)?.reference??null;
  const blocked=(representative?.items??[]).filter(item=>item.status==='blocked');
  // P6: qualification counts are read from the server verdict, never recomputed
  // here -- two implementations of the same rule would drift.
  const qualified=data.quotes.filter(quote=>quote.qualification?.tier==='qualified').length;
  const referenceOnly=data.quotes.filter(quote=>quote.qualification?.tier==='reference').length;
  const excluded=data.quotes.filter(quote=>quote.qualification?.tier==='excluded').length;
  const notEvaluated=data.quotes.filter(quote=>quote.qualification===null).length;
  const lowIndex=data.qualified_lowest_id===null?-1:data.quotes.findIndex(quote=>quote.id===data.qualified_lowest_id);
  const low=lowIndex>=0?data.quotes[lowIndex]:null;
  const qualifiedState=qualified>0?'some':data.quotes.length>0?'none':'empty';
  return <section className="quote-evidence" aria-label="报价资格与费用核对">
    <div className="evidence-heading">
      <h5>资格与费用核对</h5>
      <span>参考层 · 非可订实付价</span>
    </div>
    {data.occupancy_status!=='matched'&&<p className="evidence-context-warning">
      来源入住人数/房数回显：{data.occupancy_status==='mismatch'?'不一致':'未完整核实'}。
      {data.occupancy_context_issues.length>0
        ?safeIssueLabels(data.occupancy_context_issues):'已按“请求参数＋剩余房量＋观察时间”推定，来源未回显。'}
    </p>}
    {data.booking_status!=='matched'&&<p className="evidence-context-warning">
      安全来源跳转：{data.booking_status==='mismatch'?'链接上下文不匹配，已停用':data.booking_status==='unavailable'?'链接不可用':'链接上下文未完整核实'}。
      {data.booking_context_issues.length>0
        ?safeIssueLabels(data.booking_context_issues):'来源详情不返回预订链接，本页不提供跳转。'}
      不会改写来源链接来匹配本次条件。
    </p>}
    <p className="evidence-rate-count">{data.source.name}返回{data.quotes.length}条报价方案（不是可订房间数）：需二次确认{onRequest}条、未标记需申请{notMarked}条、申请状态未知{requestUnknown}条。未标记需申请也不代表有库存。{capacityUnknown>0 && `其中${capacityUnknown}条缺少单间人数上限。`}</p>
    <p className={'evidence-qualified qualified-'+qualifiedState}>
      {qualified>0
        ?<>本轮{data.quotes.length}条报价中<strong>{qualified}</strong>条合格（含推定标注）
          {low!==null&&low.qualification?.fees.per_room_night_fen!==null&&low.qualification?.fees.per_room_night_fen!==undefined
            &&<>；最低合格报价为第{lowIndex+1}条，已知费用折算每房每晚¥{money(low.qualification.fees.per_room_night_fen)}</>}。
          「合格」表示资格条件与预算均未发现问题，<strong>不代表可订</strong>，也不代表已含全部费用。</>
        :<>{data.quotes.length>0
          ?<>本轮没有合格报价：{referenceOnly}条仅参考、{excluded}条已排除{notEvaluated>0&&<>、{notEvaluated}条未评估</>}。被排除与仅参考的原因逐条写在各自报价里。</>
          :<>本轮没有报价，因此没有合格判定。</>}</>}
    </p>
    {representative===null
      ?<p className="evidence-context-warning">
        本次未评估参考层资格（本次没有可评估的报价，或来源未返回所需字段），因此不展示任何“参考价”判定。
      </p>
      :<>
        <dl className="evidence-grid">
          {representative.items.map(item=><div key={item.field}>
            <dt>{item.label}</dt>
            <dd>
              <span>{statusText[item.status]}</span>
              <p>{item.note}</p>
            </dd>
          </div>)}
        </dl>
        <p className="evidence-rate-count">
          参考层：{data.quotes.length}条报价中<strong>{displayable}</strong>条可作为带标注的参考价展示；
          逐条判定，房型写“公卫／床位”或剩余房量不足本次{data.context.rooms}间的会单独拦下。
          {evaluated<data.quotes.length && `另有${data.quotes.length-evaluated}条未评估。`}
        </p>
        {blocked.length>0&&<p className="evidence-request">
          该报价被拦下的项目：{blocked.map(item=>item.label).join('、')}。被拦下时不展示为参考价。
        </p>}
      </>}
    {onRequest>0&&<p className="evidence-request">
      {onRequest}个已返回报价标为需二次确认；其他报价也未证明足量库存。
    </p>}
    {data.quotes.length===0
      ?<p className="evidence-footnote">
        本次没有返回报价，不代表整个城市无房，也不能确定是哪项条件导致。
      </p>
      :<p className="evidence-footnote">
        参考层允许“按国内默认推定的整间与独卫”“展示价已含税”“请求参数推定入住组合”；
        押金／到店必付与来源链接只作警示，页面始终不声称完整含费、不提供跳转。
        可订层不接受任何推定，因此{nights}晚{data.context.rooms}间的实付总价仍需到{data.source.name}核对。
      </p>}
  </section>;
}
