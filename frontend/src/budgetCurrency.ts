/**
 * Optional exchange-rate *references* for the search form.
 * The hotel search API keeps its existing, exact CNY-fen contract.
 * Rates are not a settlement quote and never make unknown rates look valid.
 */
import {budgetFen} from './format';

export const FX_REFERENCE_URL =
  'https://api.frankfurter.dev/v2/rates?base=CNY&quotes=USD,HKD,EUR,JPY';

export const BUDGET_CURRENCIES = ['CNY','USD','HKD','EUR','JPY'] as const;
export type BudgetCurrency = typeof BUDGET_CURRENCIES[number];
export type OtherCurrency = Exclude<BudgetCurrency,'CNY'>;
export type FxRates = {
  date:string;
  perCny:Record<OtherCurrency,number>;
};
const REQUIRED:OtherCurrency[] = ['USD','HKD','EUR','JPY'];
const DAY_MS = 86400000;

export function parseFxRates(raw:unknown, now:Date = new Date()):FxRates {
  if(!Array.isArray(raw)) throw new Error('汇率服务格式异常');
  const found = new Map<OtherCurrency,{rate:number;date:string}>();
  for(const row of raw) {
    if(typeof row!=='object'||row===null) throw new Error('汇率服务格式异常');
    const item = row as Record<string,unknown>;
    if(item.base!=='CNY'||!REQUIRED.includes(item.quote as OtherCurrency)||
      typeof item.rate!=='number'||!Number.isFinite(item.rate)||item.rate<=0||
      typeof item.date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(item.date)) {
      throw new Error('汇率服务返回了无效报价');
    }
    const currency=item.quote as OtherCurrency;
    if(found.has(currency)) throw new Error('汇率出现重复币种');
    found.set(currency,{rate:item.rate,date:item.date});
  }
  if(found.size!==REQUIRED.length) throw new Error('汇率缺少必要币种');
  const date=found.get('USD')!.date;
  if(!REQUIRED.every(currency=>found.get(currency)!.date===date)) {
    throw new Error('汇率数据日期不一致');
  }
  const quoteDay=Date.parse(date+'T00:00:00Z');
  const today=Date.parse(now.toISOString().slice(0,10)+'T00:00:00Z');
  if(!Number.isFinite(quoteDay)||quoteDay>today+DAY_MS||today-quoteDay>7*DAY_MS){
    throw new Error('汇率日期已失效');
  }
  const perCny=Object.fromEntries(
    REQUIRED.map(currency=>[currency,found.get(currency)!.rate])
  ) as Record<OtherCurrency,number>;
  return {date,perCny};
}

/** Returns a maximum budget in CNY cents; floors conversions to avoid inflating it. */
export function toCnyBudgetFen(value:string,currency:BudgetCurrency,rates:FxRates|null):number|null {
  const units=budgetFen(value);
  if(units===null||currency==='CNY')return units;
  if(!rates)throw new Error('汇率暂时不可用，请切回人民币或稍后重试。');
  const rate=rates.perCny[currency];
  if(!Number.isFinite(rate)||rate<=0)throw new Error('汇率暂时不可用，请切回人民币。');
  const result=Math.floor(units/rate+1e-8);
  if(!Number.isSafeInteger(result)||result>100000000){
    throw new Error('换算后预算超出支持范围，请调整。');
  }
  return result;
}

export function stayBudgetCeiling(cnyFen:number|null,rooms:number,nights:number):number|null {
  if(cnyFen===null||!Number.isInteger(rooms)||rooms<1||!Number.isInteger(nights)||nights<1||nights>28) return null;
  const total=cnyFen*rooms*nights;
  return Number.isSafeInteger(total)?total:null;
}

export function formatCurrencyAmount(amount:number,currency:BudgetCurrency):string {
  const symbols:Record<BudgetCurrency,string>={
    CNY:'¥',USD:'US$',HKD:'HK$',EUR:'€',JPY:'JP¥',
  };
  const digits=new Intl.NumberFormat('zh-CN',{
    minimumFractionDigits:currency==='CNY'||currency==='JPY'?0:2,
    maximumFractionDigits:currency==='JPY'?0:2,
  }).format(amount);
  return symbols[currency]+digits;
}
