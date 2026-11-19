import type {QuotesResponse, RecallResponse, SearchResponse} from './types';

type SearchResult = SearchResponse | RecallResponse;

export type CandidateState = 'reference' | 'empty' | 'unavailable' | 'test';

export function assessCandidateEvidence(result: SearchResult): {
  state: CandidateState;
  title: string;
  detail: string;
} {
  if (result.data_mode === 'test') {
    return {state: 'test', title: '模拟候选，仅供测试', detail: '本次并非真实来源，不能作为酒店身份、报价或路线的验收证据。'};
  }
  if (result.status === 'unavailable') {
    return {state: 'unavailable', title: '来源暂不可用', detail: '本次未完成可靠查询，不能把来源错误视为没有酒店。'};
  }
  if (result.hotels.length === 0) {
    return {state: 'empty', title: '暂未找到候选', detail: '本次覆盖有限，不能据此认定整个城市没有符合条件的住宿。'};
  }
  return {
    state: 'reference',
    title: '已取得来源候选',
    detail: '可查看来源酒店、参考价格与当前条件。酒店身份和交通须分别核验；参考价不等于可订实付价。',
  };
}

export function assessBookabilityEvidence(detail: QuotesResponse): {
  status: 'ready' | 'unverified';
  sourceContext: string;
  sourceLink: string;
  missing: readonly string[];
  notes: readonly string[];
  evaluated: boolean;
} {
  // The booking layer is decided on the server by the same rule module the
  // reference layer uses; this panel must not re-derive it. `bookability`
  // null/undefined means "not evaluated" and can never read as a pass.
  // Matched URL/occupancy alone must NEVER promote a returned rate plan.
  const gate = detail.bookability ?? null;
  const sourceContext = detail.occupancy_status === 'matched'
    ? '供应商人数与房数回显一致（仍不证明库存）'
    : detail.occupancy_status === 'mismatch'
      ? '供应商人数或房数回显冲突'
      : '供应商人数与房数回显未完整核实';
  const sourceLink = detail.booking_status === 'matched'
    ? '来源链接参数已核对（不是已确认可订）'
    : detail.booking_status === 'mismatch'
      ? '来源链接条件冲突，已停用'
      : detail.booking_status === 'unavailable'
        ? '来源链接不可用'
        : '来源链接条件未完整核实';
  const blocked = (gate?.items ?? []).filter(item => item.status !== 'verified');
  const notes = Array.from(new Set(blocked.map(item => item.note).filter(note => note !== '')));
  return {
    status: gate?.ready === true ? 'ready' : 'unverified',
    sourceContext,
    sourceLink,
    missing: gate === null
      ? ['可订层本次未评估（没有可评估的详情响应）']
      : blocked.map(item => item.label),
    notes,
    evaluated: gate !== null,
  };
}

export function CapabilityStages({result}: {result: SearchResult}) {
  const candidate = assessCandidateEvidence(result);
  return <section className="capability-stages" aria-label="两层产品能力与状态">
    <div className="capability-stage">
      <span className="capability-stage-index">第一层 · 智能找酒店</span>
      <strong>{candidate.title}</strong>
      <p>{candidate.detail}</p>
    </div>
    <div className="capability-stage">
      <span className="capability-stage-index">第二层 · 可订房型验证</span>
      <strong>尚未证明符合全部预订条件</strong>
      <p>须逐项核实同房型入住资格、独享独卫、足量库存、完整含费价格及安全来源访问；未知项目不自动合格。</p>
    </div>
  </section>;
}

export function BookabilityGate({data}: {data: QuotesResponse}) {
  const evidence = assessBookabilityEvidence(data);
  return <section className="bookability-gate" aria-label="房型可订资格状态">
    <div className="bookability-gate-title">
      <strong>预订前，还需要确认这些信息</strong>
      <span>{data.data_mode==='test'?'模拟数据':evidence.status==='ready'?'已核对关键条件':'部分条件待确认'}</span>
    </div>
    <p className="bookability-summary">{evidence.status==='ready'
      ? '来源已提供关键入住条件的核对结果，实际价格与预订情况仍以供应商页面为准。'
      : '目前看到的是房型参考价格；入住人数、库存或完整费用仍有待核实，暂不能视为已可预订。'}</p>
    <details className="bookability-evidence">
      <summary>查看详细核验情况</summary>
      <div className="bookability-evidence-body">
        <strong>第二层 · 可订资格：{evidence.status === 'ready' ? '来源已逐项核实通过' : '尚未核实通过'}</strong>
        <p>当前有{data.quotes.length}条来源报价方案，代表方案条目，不代表房间库存或已可预订。</p>
        <p>{evidence.sourceContext}；{evidence.sourceLink}。</p>
        {evidence.status === 'ready'
          ? <p>可订层门禁判定通过：来源已逐项提供入住组合回显、押金／到店必付与可核验来源链接。仍以来源页面为准。</p>
          : <>
            <p>
              {evidence.evaluated ? '可订层门禁判定未通过' : '可订层本次未评估'}
              ，仍缺：{evidence.missing.join('、')}。即使以上两个参数状态均为“匹配”，也不能直接认定可订。
            </p>
            {evidence.notes.length > 0 && <p>判定口径：{evidence.notes[0]}</p>}
          </>}
      </div>
    </details>
  </section>;
}
