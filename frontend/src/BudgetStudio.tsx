import type {BudgetCurrency,FxRates} from './budgetCurrency';
import {BUDGET_CURRENCIES,formatCurrencyAmount,stayBudgetCeiling,toCnyBudgetFen} from './budgetCurrency';

type Props={
 value:string;
 currency:BudgetCurrency;
 rates:FxRates|null;
 fxOpen:boolean;
 fxStatus:'idle'|'loading'|'ready'|'error';
 rooms:number;
 nights:number;
 offline:boolean;
 onValueChange:(value:string)=>void;
 onCurrencyChange:(value:BudgetCurrency)=>void;
 onToggleFx:()=>void;
 onRetryFx:()=>void;
};

const LABELS:Record<BudgetCurrency,string>={
 CNY:'人民币 CNY',USD:'美元 USD',HKD:'港币 HKD',EUR:'欧元 EUR',JPY:'日元 JPY',
};
const SYMBOLS:Record<BudgetCurrency,string>={
 CNY:'¥',USD:'$',HKD:'HK$',EUR:'€',JPY:'¥',
};

export default function BudgetStudio({
 value,currency,rates,fxOpen,fxStatus,rooms,nights,offline,
 onValueChange,onCurrencyChange,onToggleFx,onRetryFx,
}:Props){
 let cnyFen:number|null=null;
 let inputError='';
 try{ cnyFen=toCnyBudgetFen(value,currency,rates); }
 catch(reason){ inputError=reason instanceof Error?reason.message:'金额有误'; }
 const totalFen=stayBudgetCeiling(cnyFen,rooms,nights);
 const canConvert=Boolean(rates&&!inputError&&cnyFen!==null);
 const cnyValue=cnyFen!==null?cnyFen/100:null;
 return <section className="budget-studio" aria-label="住宿预算参考">
   <div className="budget-studio-main">
     <div className="budget-intro">
       <span className="budget-eyebrow">可选偏好</span>
       <h3>每房每晚预算</h3>
       <p>输入你的心理价位，按每间房、每一晚计算。</p>
     </div>
     <div className="budget-entry-col">
       <label htmlFor="budget">每晚预算参考 <span>每房</span></label>
       <div className="budget-entry">
         <select aria-label="预算币种" value={currency} onChange={event=>onCurrencyChange(event.target.value as BudgetCurrency)}>
           {BUDGET_CURRENCIES.map(code=><option value={code} key={code}>{LABELS[code]}</option>)}
         </select>
         <span className="budget-symbol" aria-hidden="true">{SYMBOLS[currency]}</span>
         <input id="budget" aria-label="预算金额" inputMode="decimal" autoComplete="off"
           maxLength={10} placeholder="例如 300" value={value} onChange={e=>onValueChange(e.target.value)}
           aria-describedby="budget-context"/>
       </div>
     </div>
     <div className="budget-context" id="budget-context" aria-live="polite">
       {value.trim()==='' ? <>
         <strong>不填则不限预算</strong>
         <span>酒店查询不会加上价格上限。</span>
       </> : inputError ? <>
         <strong>预算待确认</strong><span>{inputError}</span>
       </> : <>
         <strong>{formatCurrencyAmount(cnyValue!,'CNY')} <small>每房每晚 · 约</small></strong>
         {totalFen===null
           ? <span>日期确认后展示整段行程预算上限。</span>
           : <span>{rooms}间房 × {nights}晚 · 上限约 {formatCurrencyAmount(totalFen/100,'CNY')}</span>}
       </>}
     </div>
   </div>
   <div className="budget-bottom">
     <button className="budget-fx-toggle" type="button" aria-expanded={fxOpen}
       aria-controls="budget-conversion" onClick={onToggleFx}>
       <span aria-hidden="true">⇄</span> {fxOpen?'收起参考换算':'查看参考换算'}
       <span className="budget-fx-chevron" aria-hidden="true">{fxOpen?'⌃':'⌄'}</span>
     </button>
     <span>参考汇率用于估算 · 预算上限，不是最终房价</span>
   </div>
   {fxOpen&&<div id="budget-conversion" className="budget-conversion" aria-live="polite">
     {offline ? <p>本地演示不联网获取外币汇率；可直接使用人民币预算。</p>
     : fxStatus==='loading' ? <p role="status">正在读取参考汇率…</p>
     : fxStatus==='error'||!rates ? <div className="budget-fx-unavailable">
       <p>汇率暂时不可用。人民币预算仍可正常使用，外币金额暂不用于查询。</p>
       <button type="button" onClick={onRetryFx}>重试获取汇率</button>
     </div>
     : <>
       <div className="budget-fx-head">
         <strong>参考汇率日期：{rates.date}</strong>
         <span>Frankfurter · 日度参考汇率，非实时成交价</span>
       </div>
       <div className="budget-fx-grid">
         {BUDGET_CURRENCIES.filter(code=>code!==currency).map(code=>{
           const converted=cnyValue===null?null:code==='CNY'?cnyValue:cnyValue*rates.perCny[code];
           return <div className="budget-fx-item" key={code}>
             <small>{code}</small>
             <strong>{canConvert&&converted!==null?formatCurrencyAmount(converted,code):'—'}</strong>
           </div>;
         })}
       </div>
       <p className="budget-fx-disclaimer">仅供比较币种和行程预算，不是银行结算汇率；最终酒店费用以来源及实际支付为准。</p>
     </>}
   </div>}
 </section>;
}
