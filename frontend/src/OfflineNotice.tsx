import type {Health} from './types';

export const OFFLINE_LABEL = '广州离线 Agent 测试夹具 · 非实时查源';
export const DEMO_SOURCE_NAME = '广州测试夹具';

export function isOffline(health: Health|null|undefined): boolean {
  return health?.data_mode === 'offline';
}

export default function OfflineNotice({health}:{health:Health|null}) {
  if (!isOffline(health)) return null;
  const dataset = health?.dataset;
  return <div className="offline-notice" role="status">
    <strong>{OFFLINE_LABEL}</strong>
    <span>{dataset ? `${dataset.id} v${dataset.version}` : '固定合成数据集'}</span>
    <p>企业 MCP 接口权限暂不可用，本夹具只覆盖广州，用于测试 Agent 的条件、资格、费用与返程规则；虚构样例配合成值，不连接第三方接口，不提供可订报价或真实预订链接。</p>
  </div>;
}
