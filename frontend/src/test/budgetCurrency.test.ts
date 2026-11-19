import {describe,expect,it} from 'vitest';
import {parseFxRates,toCnyBudgetFen,formatCurrencyAmount,stayBudgetCeiling} from '../budgetCurrency';

const now=new Date('2026-11-12T06:00:00Z');
const quotes=[
 {base:'CNY',quote:'USD',rate:0.14,date:'2026-11-11'},
 {base:'CNY',quote:'HKD',rate:1.09,date:'2026-11-11'},
 {base:'CNY',quote:'EUR',rate:0.13,date:'2026-11-11'},
 {base:'CNY',quote:'JPY',rate:22,date:'2026-11-11'},
];
const rates=parseFxRates(quotes,now);

describe('optional stay budget in multiple reference currencies',()=>{
 it('validates fixed supported currency quotes, preserves the source date and prevents invented rates',()=>{
  expect(rates.date).toBe('2026-11-11');
  expect(rates.perCny.USD).toBe(0.14);
  expect(()=>parseFxRates(quotes.slice(0,3),now)).toThrow();
  expect(()=>parseFxRates([{...quotes[0],rate:-1},...quotes.slice(1)],now)).toThrow();
  expect(()=>parseFxRates([{...quotes[0],date:'2026-10-15'},...quotes.slice(1)],now)).toThrow();
  expect(()=>parseFxRates({...quotes} as unknown,now)).toThrow();
 });
 it('preserves the backend CNY-fen contract and rounds a foreign-currency maximum down',()=>{
  expect(toCnyBudgetFen('', 'USD',null)).toBeNull();
  expect(toCnyBudgetFen('199.29','CNY',null)).toBe(19929);
  expect(toCnyBudgetFen('100','USD',rates)).toBe(71428);
  expect(toCnyBudgetFen('218','HKD',rates)).toBe(20000);
  expect(()=>toCnyBudgetFen('100','USD',null)).toThrow(/汇率/);
  expect(()=>toCnyBudgetFen('0.001','USD',rates)).toThrow(/小数/);
  expect(()=>toCnyBudgetFen('-1','CNY',null)).toThrow();
 });
 it('uses human-friendly amounts rather than raw ISO codes in the budget summary',()=>{
  expect(formatCurrencyAmount(300,'CNY')).toBe('¥300');
  expect(formatCurrencyAmount(300.25,'CNY')).toBe('¥300.25');
  expect(formatCurrencyAmount(45.01,'USD')).toBe('US$45.01');
  expect(formatCurrencyAmount(353.4,'HKD')).toBe('HK$353.40');
  expect(formatCurrencyAmount(7166.7,'JPY')).toBe('JP¥7,167');
 });
 it('never confuses total-stay budget ceiling with a live hotel quote',()=>{
  expect(stayBudgetCeiling(20000,2,3)).toBe(120000);
  expect(stayBudgetCeiling(null,2,3)).toBeNull();
  expect(stayBudgetCeiling(20000,0,3)).toBeNull();
  expect(formatCurrencyAmount(28,'USD')).toContain('28');
  expect(formatCurrencyAmount(4400,'JPY')).toContain('4,400');
 });
});
