export function chinaDate(offset = 0, now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
  const get = (type: string) => Number(parts.find(part => part.type === type)?.value);
  return new Date(Date.UTC(get('year'), get('month') - 1, get('day') + offset)).toISOString().slice(0,10);
}
export function addDays(day: string, count: number): string {
  const date = new Date(day + 'T00:00:00Z');
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0,10);
}
export function budgetFen(value: string): number|null {
  if (!value.trim()) return null;
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(value.trim())) throw new Error('预算请输入非负金额，最多两位小数。');
  const [whole, fraction = ''] = value.trim().split('.');
  const amount = Number(whole) * 100 + Number(fraction.padEnd(2,'0'));
  if (!Number.isSafeInteger(amount) || amount > 100000000) throw new Error('预算数额过大，请调整。');
  return amount;
}
export function money(fen: number|null): string {
  return fen !== null && Number.isSafeInteger(fen) && fen >= 0
    ? new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(fen / 100) : '待核实';
}
export function timeText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '查询时间未取得' : new Intl.DateTimeFormat('zh-CN',{
    timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,
  }).format(date);
}
export function stale(value: string, now = Date.now()): boolean {
  return now - new Date(value).getTime() > 5 * 60 * 1000;
}
export function safeBooking(value: string|null): string|null {
  if (!value || value.includes('[REDACTED]')) return null;
  try {
    const url = new URL(value);
    const allowed = ['rollinggo.cn','rollinggo.store'].some(domain => url.hostname === domain || url.hostname.endsWith('.' + domain));
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && allowed ? value : null;
  } catch { return null; }
}
// Lawful entry to the supplier itself, for the cases where the original
// booking link cannot be verified and must stay disabled (spec R09).
// Each entry is a fixed homepage we write ourselves -- never a supplier URL
// that we rewrote or re-signed.
const SOURCE_ENTRIES: Record<string, string> = {
  rollinggo: 'https://rollinggo.cn/',
  tuniu: 'https://www.tuniu.com/',
};
// Entry hosts are allowlisted separately from booking-link hosts: an entry is
// a static homepage, while a booking link is supplier data and keeps the
// stricter allowlist in safeBooking().
const ENTRY_HOSTS = ['rollinggo.cn', 'rollinggo.store', 'tuniu.com'];
function safeEntry(value: string): string|null {
  try {
    const url = new URL(value);
    const allowed = ENTRY_HOSTS.some(domain => url.hostname === domain || url.hostname.endsWith('.' + domain));
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && allowed ? value : null;
  } catch { return null; }
}
export function sourceEntry(id: string): string|null {
  const value = SOURCE_ENTRIES[id];
  return value ? safeEntry(value) : null;
}
