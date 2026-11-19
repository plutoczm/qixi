import type {SearchResponse} from './types';

type JourneyState = 'idle' | 'active' | 'observed' | 'blocked';

type JourneyStep = {
  number: string;
  title: string;
  caption: string;
  state: JourneyState;
};

export function buildJourney(result: SearchResponse | null, loading: boolean, offline: boolean): JourneyStep[] {
  const attempted = loading || result !== null;
  const unavailable = result?.status === 'unavailable';
  const simulated = offline || result?.data_mode === 'test';

  const sourceCaption = loading
    ? '正在请求'
    : unavailable
      ? '来源不可用'
      : result === null
        ? '尚未调用'
        : simulated
          ? '模拟读取'
          : result.status === 'partial'
            ? '部分响应'
            : '已收到响应';

  const candidateCaption = result === null
    ? '等待结果'
    : unavailable
      ? '无法判断'
      : result.hotels.length === 0
        ? '本次无候选'
        : `${result.hotels.length} 家${simulated ? '模拟' : '参考'}`;

  return [
    {number: '01', title: '条件', caption: attempted ? '本次已提交' : '待提交', state: attempted ? 'observed' : 'idle'},
    {number: '02', title: '来源', caption: sourceCaption, state: loading ? 'active' : unavailable ? 'blocked' : result ? 'observed' : 'idle'},
    {number: '03', title: '候选', caption: candidateCaption, state: result && !unavailable && result.hotels.length > 0 ? 'observed' : 'idle'},
    {number: '04', title: '房型', caption: '打开后核验', state: 'idle'},
    {number: '05', title: '返程', caption: '另行查源', state: 'idle'},
  ];
}

export default function EvidenceJourney({result, loading, offline}: {
  result: SearchResponse | null;
  loading: boolean;
  offline: boolean;
}) {
  const steps = buildJourney(result, loading, offline);

  return <nav className="evidence-journey" aria-label="查询证据进度">
    <div className="journey-heading">
      <span>THE EVIDENCE TRAIL</span>
      <small>查询进度 ≠ 已验证可订</small>
    </div>
    <ol className="journey-track">
      {steps.map(step => <li key={step.number} className={'journey-step is-'+step.state}>
        <span className="journey-marker" aria-hidden="true">{step.number}</span>
        <span className="journey-copy">
          <strong>{step.title}</strong>
          <span className="journey-caption" aria-current={step.state === 'active' ? 'step' : undefined}>{step.caption}</span>
        </span>
      </li>)}
    </ol>
  </nav>;
}
