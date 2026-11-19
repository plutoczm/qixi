import {describe,it,expect} from 'vitest';
import {addDays,budgetFen,chinaDate,money,safeBooking,sourceEntry,stale} from '../format';
describe('amounts and dates',()=>{
 it('keeps fen exact and does not turn unknown into zero',()=>{expect(budgetFen('0.29')).toBe(29);expect(budgetFen('')).toBeNull();expect(money(null)).toBe('待核实');expect(budgetFen('200.01')).toBe(20001);expect(()=>budgetFen('200.001')).toThrow();});
 it('uses China date across UTC midnight and leap years',()=>{expect(chinaDate(0,new Date('2026-10-15T17:00:00Z'))).toBe('2026-10-16');expect(addDays('2028-02-28',1)).toBe('2028-02-29');});
 it('marks quotes stale only after five minutes',()=>{const at='2026-10-16T00:00:00Z';expect(stale(at,Date.parse(at)+300000)).toBe(false);expect(stale(at,Date.parse(at)+300001)).toBe(true);});
});
describe('booking handoff',()=>{
 it.each(['https://rollinggo.cn/book?a=1','https://www.rollinggo.store/book'])('preserves official URL %s',url=>expect(safeBooking(url)).toBe(url));
 it.each(['javascript:alert(1)','http://rollinggo.cn/','https://rollinggo.cn.evil.test/','https://u:p@rollinggo.cn/','https://rollinggo.cn:999/','https://evil.test/','https://rollinggo.cn/[REDACTED]'])('rejects unsafe URL %s',url=>expect(safeBooking(url)).toBeNull());
 it('offers a lawful supplier entry only for allowlisted sources',()=>{expect(sourceEntry('rollinggo')).toBe('https://rollinggo.cn/');expect(sourceEntry('tuniu')).toBe('https://www.tuniu.com/');expect(sourceEntry('flyai')).toBeNull();expect(sourceEntry('')).toBeNull();expect(sourceEntry('evil')).toBeNull();});
});
