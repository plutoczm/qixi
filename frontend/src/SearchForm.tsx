import {useEffect, useState} from 'react';
import type {FormEvent} from 'react';
import {addDays, chinaDate} from './format';
import {saveRecentSearch, readRecentSearch, clearRecentSearch} from './recentSearch';
import type {PlaceType, SearchInput} from './types';
import type {TripHandoff} from './tripIntent';
import BudgetStudio from './BudgetStudio';
import {FX_REFERENCE_URL,parseFxRates,toCnyBudgetFen} from './budgetCurrency';
import type {BudgetCurrency,FxRates} from './budgetCurrency';

export type RecallOptions = Omit<import('./types').RecallInput,'operation_id'|'query'>;
type Props = {loading:boolean;preset?:SearchInput;trip?:TripHandoff|null;sourceName?:string;offline?:boolean;onSubmit:(input:SearchInput)=>Promise<void>;onRecall?:(input:SearchInput,options:RecallOptions)=>Promise<void>};
export default function SearchForm({loading,preset,trip=null,onSubmit,onRecall,sourceName='道旅',offline=false}:Props) {
  const [place,setPlace] = useState('');
  const [placeType,setPlaceType] = useState<PlaceType>('城市');
  // A mainland-only product has no mutable country selector.
  const [waypoints,setWaypoints] = useState<string[]>([]);
  const [checkIn,setCheckIn] = useState(chinaDate(1));
  const [checkOut,setCheckOut] = useState(chinaDate(2));
  const [adults,setAdults] = useState(2);
  const [rooms,setRooms] = useState(1);
  const [ages,setAges] = useState<string[]>([]);
  const [budget,setBudget] = useState('');
  const [budgetCurrency,setBudgetCurrency]=useState<BudgetCurrency>('CNY');
  const [fxOpen,setFxOpen]=useState(false);
  const [fxRates,setFxRates]=useState<FxRates|null>(null);
  const [fxStatus,setFxStatus]=useState<'idle'|'loading'|'ready'|'error'>('idle');
  const [fxAttempt,setFxAttempt]=useState(0);
  const [acceptsDormitory,setAcceptsDormitory] = useState(false);
  const [limit,setLimit] = useState(20);
  const [error,setError] = useState('');
  const [saved,setSaved] = useState<SearchInput|null>(()=>readRecentSearch());
  const [multi,setMulti]=useState(false);const [map,setMap]=useState<'amap'|'tencent'>('amap');
  const [strategy,setStrategy]=useState<'branch_place'|'map_hotels'|'named_hotel'>('branch_place');
  const [keyword,setKeyword]=useState('海底捞');const [branchLimit,setBranchLimit]=useState<3|5>(3);
  const [namedName,setNamedName]=useState('');
  const [namedAddress,setNamedAddress]=useState('');
  const [restaurantAddress,setRestaurantAddress]=useState('');
  // Reference FX is requested ONLY after the user opens it or selects a
  // foreign currency. No supplier call, conversion amount or travel details
  // are ever sent to the FX provider.
  useEffect(()=>{
    if(!fxOpen||offline||fxRates)return;
    let active=true;
    const controller=new AbortController();
    const timeout=window.setTimeout(()=>controller.abort(),7000);
    setFxStatus('loading');
    void fetch(FX_REFERENCE_URL,{signal:controller.signal}).then(async response=>{
      if(!response.ok)throw new Error('汇率请求失败');
      return response.json() as Promise<unknown>;
    }).then(data=>{
      const parsed=parseFxRates(data);
      if(active){setFxRates(parsed);setFxStatus('ready');}
    }).catch(()=>{
      if(active)setFxStatus('error');
    }).finally(()=>window.clearTimeout(timeout));
    return ()=>{active=false;controller.abort();window.clearTimeout(timeout);};
  },[fxOpen,offline,fxRates,fxAttempt]);

  function chooseBudgetCurrency(next:BudgetCurrency) {
    if(next===budgetCurrency)return;
    // Never reinterpret an entered number as a different currency.
    setBudget('');setBudgetCurrency(next);setError('');
    if(next!=='CNY')setFxOpen(true);
  }
  function applyInput(input:SearchInput) {
    if(input.country_region && !['中国','中国大陆','中华人民共和国','china','mainland china','prc'].includes(input.country_region.toLocaleLowerCase())){
      setError('旧条件位于中国大陆以外，请重新填写国内目的地。');return;
    }
    setPlace(input.place);setPlaceType(input.place_type);
    setWaypoints([...(input.waypoints??[])]);setCheckIn(input.check_in);setCheckOut(input.check_out);
    setAdults(input.adults);setRooms(input.rooms);setAges(input.children_ages.map(String));
    setLimit(input.limit);setBudgetCurrency('CNY');setBudget(input.budget_fen === null ? '' : String(input.budget_fen/100));
    setAcceptsDormitory(input.accepts_dormitory === true);setError('');
  }
  useEffect(() => {if(preset)applyInput(preset);},[preset]);
  const canRecall=Boolean(onRecall);
  useEffect(()=>{
    if(!trip)return;
    const stay=trip.visits.find(item=>item.name===trip.stayAnchor);
    if(stay){
      setPlace(stay.name);
      setPlaceType(stay.category==='景点'?'景点':'详细地址');
      setWaypoints(trip.visits.filter(item=>item.name!==stay.name).map(item=>item.name).slice(0,5));
      setMulti(false);
    }else if(trip.restaurantAnchor&&canRecall){
      // Compatibility only: an explicit legacy restaurant opt-in.
      setMulti(true);setStrategy('branch_place');setKeyword(trip.restaurantAnchor);
    }else{
      setMulti(false);
    }
  },[trip,canRecall]);
  function currentInput():SearchInput {
    if(!place.trim())throw new Error('请填写希望住宿附近的目的地。');
    if(waypoints.some(stop=>!stop.trim()))throw new Error('请填写途经点，或移除空的途经点。');
    if(new Set(waypoints.map(stop=>stop.trim().toLocaleLowerCase())).size!==waypoints.length)
      throw new Error('途经点不能重复。');
    if(!checkIn || !checkOut)throw new Error('请填写完整的入住和退房日期。');
    if(checkIn<chinaDate())throw new Error('入住日期已过去，请重新选择。');
    if(checkOut<=checkIn || checkOut>addDays(checkIn,28))throw new Error('退房日期须在入住后1至28晚内。');
    if(ages.some(age=>!/^\d{1,2}$/.test(age)||Number(age)>17))throw new Error('请补齐每位儿童的年龄（0—17岁）。');
    return {place:place.trim(),place_type:placeType,check_in:checkIn,check_out:checkOut,
      adults,rooms,children_ages:ages.map(Number),budget_fen:toCnyBudgetFen(budget,budgetCurrency,fxRates),limit,poi_name:null,
      country_region:'中国大陆',waypoints:waypoints.map(stop=>stop.trim()),
      accepts_dormitory:acceptsDormitory};
  }
  function saveDraft() {
    try {
      const input=currentInput();
      if(!saveRecentSearch(input))throw new Error('暂存失败，请检查日期或浏览器的会话存储设置。');
      setSaved(input);setError('');
    }catch(reason){setError(reason instanceof Error?reason.message:'暂存失败，请检查条件。');}
  }
  function restoreDraft() {
    const input=readRecentSearch();
    if(!input){setSaved(null);setError('已保存的日期无效或已过期，请重新填写。');return;}
    applyInput(input);setMulti(false);setStrategy('branch_place');setSaved(input);
  }
  function clearDraft() {
    clearRecentSearch();setSaved(null);setError('');
  }
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const input=currentInput();
      if(multi&&onRecall){
        if(placeType!=='城市')throw new Error('餐饮专项门店查询只支持以城市作为主地点。');
        // All paths, including restaurant-specific recall, use mainland-only scope.
        if(!keyword.trim())throw new Error('请填写餐厅关键词。');
        if(strategy==='named_hotel'&&(!namedName.trim()||!namedAddress.trim()||!restaurantAddress.trim())){
          throw new Error('点名核验须填写餐厅完整名称和地址，以及酒店完整名称和地址。');
        }
        setError('');
        await onRecall(input,{provider:map,restaurant_keyword:keyword.trim(),
          branch_limit:branchLimit,strategy,
          named_hotel:strategy==='named_hotel'?{name:namedName.trim(),address:namedAddress.trim()}:null,
          restaurant_address:strategy==='named_hotel'?restaurantAddress.trim():null});
      }
      else{setError('');await onSubmit(input);}
    } catch (reason) {setError(reason instanceof Error ? reason.message : '请检查查询条件。');}
  }
  const visibleNights=checkIn&&checkOut
    ?Math.round((Date.parse(checkOut+'T00:00:00Z')-Date.parse(checkIn+'T00:00:00Z'))/86400000):0;
  return <form className="search-form" onSubmit={submit} aria-label="住宿查询条件">
    <div className="form-heading"><div><span className="section-kicker">寻找住宿</span><h2>下一站，住哪里？</h2></div><p>填写这次行程的信息，查看酒店与房型参考价格。</p></div>
    <div className="search-core-grid">
      <div className="search-place-field">
        <label htmlFor="place">希望住在哪个地方附近？</label>
        <input id="place" value={place} onChange={e=>setPlace(e.target.value)} maxLength={120} required autoComplete="off" placeholder="例如：杭州、上海迪士尼、北京南站、海底捞某分店"/>
        <div className="place-meta-grid">
          <div className="mainland-scope"><span className="scope-title">服务范围</span>
            <strong>中国大陆</strong><small>仅国内目的地 · 无需填写国家</small></div>
          <div><label htmlFor="place-type">地点类别</label>
            <select id="place-type" aria-label="地点类型" value={placeType} onChange={e=>setPlaceType(e.target.value as PlaceType)}>
              <option value="城市">城市</option><option value="区/县">区 / 县</option><option value="景点">景点 / 地标</option>
              <option value="详细地址">具体地点 / 门店 / 地址</option><option value="地铁站">地铁站</option>
              <option value="火车站">火车站</option><option value="机场">机场</option><option value="酒店">指定酒店</option>
            </select></div>
        </div>
        <p className="place-hint">{offline?'离线演示数据只覆盖广州，其他城市不代表真实查询。':sourceName==='途牛'?'当前途牛只接通中国大陆城市；景点或具体门店请使用其他已接入的国内来源及地图核实。':'支持中国大陆城市、景点、商圈、车站及具体门店；酒店是否真正靠近目的地仍须地图核实。'}</p>
      </div>
      <div className="search-date-field"><div className="field-pair">
        <div><label htmlFor="check-in">入住日期</label><input id="check-in" type="date" value={checkIn} min={chinaDate()} required onChange={e=>{setCheckIn(e.target.value);if(e.target.value && e.target.value>=checkOut)setCheckOut(addDays(e.target.value,1));}}/></div>
        <div><label htmlFor="check-out">退房日期</label><input id="check-out" type="date" value={checkOut} min={checkIn?addDays(checkIn,1):chinaDate(1)} max={checkIn?addDays(checkIn,28):undefined} required onChange={e=>setCheckOut(e.target.value)}/></div>
      </div></div>
      <div className="search-guests-field"><div className="field-pair">
        <div><label htmlFor="adults">每房成人</label><select id="adults" value={adults} onChange={e=>setAdults(Number(e.target.value))}>{Array.from({length:8},(_,i)=><option key={i} value={i+1}>{i+1} 位成人</option>)}</select></div>
        <div><label htmlFor="rooms">房间数量</label><select id="rooms" value={rooms} onChange={e=>setRooms(Number(e.target.value))}>{[1,2,3,4].map(count=><option key={count} value={count}>{count} 间房</option>)}</select></div>
      </div></div>
      <div className="search-children-field">
        <label htmlFor="children">每房儿童</label>
        <select id="children" value={ages.length} onChange={e=>setAges(old=>Array.from({length:Number(e.target.value)},(_,i)=>old[i]??''))}>
          {[0,1,2,3,4].map(count=><option key={count} value={count}>{count===0?'无儿童':count+' 位儿童'}</option>)}
        </select>
      </div>
    </div>
    <section className="journey-stops" aria-label="途经点设置">
      <div className="journey-stops-heading"><div><strong>途中还会去哪里？</strong><small>可选，最多 5 个途经点</small></div>
        <button type="button" disabled={loading||waypoints.length>=5} onClick={()=>setWaypoints(old=>[...old,''])}>+ 添加途经点</button></div>
      {waypoints.map((stop,index)=><div className="journey-stop" key={index}>
        <label htmlFor={'waypoint-'+index}>途经点 {index+1}</label>
        <input id={'waypoint-'+index} value={stop} maxLength={120} disabled={loading} placeholder="如：苏州北站、机场、途中景点" onChange={event=>setWaypoints(old=>old.map((item,i)=>i===index?event.target.value:item))}/>
        <button type="button" aria-label={'移除途经点 '+(index+1)} disabled={loading} onClick={()=>setWaypoints(old=>old.filter((_,i)=>i!==index))}>移除</button>
      </div>)}
      <p className="field-note">酒店只围绕上方指定的住宿目的地查找；途经点随搜索条件保存，但不会悄悄改变酒店搜索范围，也不代表路线已核实。</p>
    </section>
    {ages.length>0&&<div className="age-fields">{ages.map((age,index)=><div key={index}><label htmlFor={'age-'+index}>儿童{index+1}年龄</label><input id={'age-'+index} type="number" min="0" max="17" required value={age} onChange={e=>setAges(old=>old.map((item,i)=>i===index?e.target.value:item))}/></div>)}</div>}
    <BudgetStudio value={budget} currency={budgetCurrency} rates={fxRates}
      fxOpen={fxOpen} fxStatus={fxStatus} rooms={rooms} nights={visibleNights} offline={offline}
      onValueChange={next=>{setBudget(next);setError('');}}
      onCurrencyChange={chooseBudgetCurrency}
      onToggleFx={()=>{
        if(!fxOpen&&!fxRates&&!offline)setFxStatus('loading');
        setFxOpen(current=>!current);
      }}
      onRetryFx={()=>{setFxStatus('loading');setFxAttempt(count=>count+1);}}/>
    {trip&&trip.visits.length>0&&<div className="trip-intent-handoff" aria-label="AI整理的行程地点">
      <strong>你计划去的地方 <small>来自描述 · 未核实门店位置</small></strong>
      <p>{trip.visits.map(item=>item.name+(item.area_hint?'（'+item.area_hint+'）':'')).join('、')}</p>
      <p>{trip.stayAnchor
        ?'已按你选择的“'+trip.stayAnchor+'”填入住宿目的地；其他地点是途经点，请再次确认具体城市及门店地址。'
        :trip.restaurantAnchor&&onRecall&&multi
          ?'已启用单独的餐饮门店专项查询；这个功能不代表通用目的地搜索。'
          :'行程地点仅供核对，没有未经确认就自动替换住宿目的地。'}</p>
      {trip.visits.length>1&&<small>途中多个地点只记录意图，不自动假定道路连通或距酒店的距离。</small>}
    </div>}
    <div className="search-options-row">
      <details className="advanced-options" open={multi||undefined}>
        <summary>更多住宿设置 <span aria-hidden="true">⌄</span></summary>
        <div className="advanced-options-body">
          <div className="criteria-explainer"><strong>酒店推荐要看的证据</strong>
            <p>房价与预算：可以参考来源报价。距离：需地图实测路线；住客评分、评价人数与真实入住人数是不同数据，来源缺失时不擅自过滤或排序。</p>
          </div>
          <div className="dorm-field"><label className="recall-toggle" htmlFor="accepts-dormitory"><input id="accepts-dormitory" type="checkbox" checked={acceptsDormitory} disabled={loading} onChange={e=>setAcceptsDormitory(e.target.checked)}/>接受青旅床位／多人间</label>
            <p className="field-note">勾选后只放宽两项：整间客房、房内独卫；接受床位也意味着可能共用卫浴，床位价按每人每晚推算。不勾选时床位房一律排除。</p></div>
          <div className="limit-field"><label htmlFor="limit">查询上限</label><select id="limit" value={limit} onChange={e=>setLimit(Number(e.target.value))}>{[6,10,20].map(value=><option key={value} value={value}>{value} 家住宿</option>)}</select></div>
          <p className="field-note">每次只查询当前条件下的部分酒店，搜索上限越小，可看到的酒店也可能越少。</p>
          {!onRecall&&sourceName==='途牛'&&<p className="field-note">当前来源支持城市酒店查询；暂不提供跨餐厅门店的周边酒店比较。</p>}
          {onRecall&&<div className="legacy-recall-activation">
            <label className="recall-toggle search-branch-choice"><input type="checkbox" checked={multi} disabled={loading} onChange={e=>setMulti(e.target.checked)}/>使用餐饮门店专项工具（可选）</label>
            <p className="field-note">这是保留的餐厅多门店查询能力，当前不是通用目的地检索。仅适用于中国境内城市，使用时才展开专用选项。</p>
          </div>}
          {onRecall&&multi&&<div className="recall-options">
            <div className="field-pair"><div><label htmlFor="recall-map">门店地图来源</label><select id="recall-map" value={map} onChange={e=>setMap(e.target.value as 'amap'|'tencent')}><option value="amap">高德地图</option><option value="tencent">腾讯地图</option></select></div>
              <div><label htmlFor="branch-limit">门店查询名额</label><select id="branch-limit" value={branchLimit} disabled={strategy==='named_hotel'} onChange={e=>setBranchLimit(Number(e.target.value) as 3|5)}><option value={3}>3家门店</option><option value={5}>5家门店</option></select></div></div>
            <label htmlFor="recall-strategy">候选查找方式</label><select id="recall-strategy" value={strategy} disabled={loading} onChange={e=>setStrategy(e.target.value as 'branch_place'|'map_hotels'|'named_hotel')}><option value="branch_place">按门店地点查住宿</option><option value="map_hotels">地图附近酒店查找</option><option value="named_hotel">指定酒店核对</option></select>
            <label htmlFor="restaurant-keyword">{strategy==='named_hotel'?'餐厅完整名称（含分店）':'餐厅关键词'}</label><input id="restaurant-keyword" value={keyword} maxLength={30} required onChange={e=>setKeyword(e.target.value)}/>
            {strategy==='map_hotels'&&<p className="field-note">会分批查找地图附近的酒店，未返回的酒店不代表没有房间。</p>}
            {strategy==='named_hotel'&&<div className="named-hotel-fields" role="group" aria-label="指定酒店严格核验">
              <label htmlFor="restaurant-address">餐厅完整地址</label><input id="restaurant-address" value={restaurantAddress} maxLength={240} required placeholder="例如：广州市荔湾区花地大道中66号" onChange={e=>setRestaurantAddress(e.target.value)}/>
              <label htmlFor="named-hotel-name">酒店完整名称（含分店）</label><input id="named-hotel-name" value={namedName} maxLength={120} required placeholder="请输入酒店完整名称" onChange={e=>setNamedName(e.target.value)}/>
              <label htmlFor="named-hotel-address">酒店完整地址</label><input id="named-hotel-address" value={namedAddress} maxLength={240} required placeholder="请输入酒店门牌地址" onChange={e=>setNamedAddress(e.target.value)}/>
              <p className="field-note">酒店与餐厅的名称、地址须分别核对；无法确定是否同一家时，不会自动匹配。</p>
            </div>}
            <p className="field-note">周边住宿仅为候选，步行路线、房型和实际可订情况需要另行查看。</p>
          </div>}
        </div>
      </details>
      <button className="button primary search-button" type="submit" disabled={loading}>{loading?<><span className="spinner"/>正在查找</>:<>查询房价 <span aria-hidden="true">→</span></>}</button>
    </div>
    <div className="search-draft-tools" aria-label="搜索条件暂存">
      <button className="save-search-draft" type="button" disabled={loading} onClick={saveDraft}>保存本次条件</button>
      {saved&&<div className="saved-search-draft"><span className="saved-search-summary">已存：{saved.place} · {saved.check_in} 至 {saved.check_out}</span><button type="button" disabled={loading} onClick={restoreDraft}>恢复已保存条件</button><button type="button" disabled={loading} onClick={clearDraft}>清除已保存条件</button></div>}
      <p>仅暂存在当前标签页，刷新可恢复；不会自动查价或上传。</p>
    </div>
    {error&&<p className="inline-error" role="alert">{error}</p>}
    <p className="form-source">{offline?'演示价格不代表实时房价':'价格来自'+sourceName+'，搜索价仅供参考；房型和最终费用以实际核实结果为准。'}</p>
  </form>;
}
