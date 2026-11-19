import {useEffect,useRef,useState} from 'react';
import TravelPanel from './TravelPanel';
import type {TransportObservation} from './TravelPanel';
import {money} from './format';
import type {Hotel,PlaceOption,SearchInput} from './types';

const sourceNames={amap:'高德',tencent:'腾讯',didi:'滴滴'};
const observedUntil=(observation:TransportObservation)=>Date.parse(observation.result.queried_at)+120000;

function comparablePlace(left:PlaceOption,right:PlaceOption) {
  return !!(left.name&&right.name&&left.address&&right.address&&left.city&&right.city)
    && left.city.replace(/市$/,'')===right.city.replace(/市$/,'');
}

function samePlace(left:PlaceOption,right:PlaceOption) {
  return comparablePlace(left,right)&&left.name===right.name&&left.address===right.address;
}

export default function HotelTransport({hotel,requestId,context,offline=false}:{
  hotel:Pick<Hotel,'id'|'name'|'address'>;requestId:string;context:SearchInput;offline?:boolean;
}) {
  const [open,setOpen]=useState(false);
  const [current,setCurrent]=useState<TransportObservation|null>(null);
  const [now,setNow]=useState(Date.now);
  const [manualPair,setManualPair]=useState<{active:TransportObservation;companion:TransportObservation}|null>(null);
  const previous=useRef<{map:TransportObservation|null;fare:TransportObservation|null}>({map:null,fare:null});
  const active=current&&observedUntil(current)>now?current:null;
  const activeIsFare=active?.result.provider==='didi';
  const companion=activeIsFare?previous.current.map:previous.current.fare;

  const canCompare=active&&companion&&observedUntil(companion)>now
    && companion.result.data_mode===active.result.data_mode
    && companion.result.request_id===active.result.request_id
    && companion.result.hotel_id===active.result.hotel_id;
  const exactPair=canCompare&&samePlace(active.origin,companion.origin)&&samePlace(active.hotel,companion.hotel);
  const manualReady=canCompare&&!exactPair
    && comparablePlace(active.origin,companion.origin)&&comparablePlace(active.hotel,companion.hotel);
  const confirmed=manualReady&&manualPair?.active===active&&manualPair.companion===companion;
  const paired=exactPair||confirmed?companion.result:null;
  const map=activeIsFare?paired:active?.result;
  const taxi=activeIsFare?active?.result:paired;
  const fare=taxi?.fares.find(item=>item.name==='快车'&&item.upper_fen!==null)
    ??taxi?.fares.find(item=>item.upper_fen!==null);
  const parts=[
    map?.return_walk?`步行约 ${Math.ceil(map.return_walk.duration_seconds/60)} 分钟 · ${sourceNames[map.provider]}`:null,
    map?.return_drive?`驾车约 ${Math.ceil(map.return_drive.duration_seconds/60)} 分钟（不含候车） · ${sourceNames[map.provider]}`:null,
    fare?.upper_fen!=null
      ? `${fare.name}当前估价 ¥${fare.lower_fen!=null&&fare.lower_fen!==fare.upper_fen
        ? money(fare.lower_fen)+'–' : ''}${money(fare.upper_fen)} · 滴滴`
      : null,
  ].filter(Boolean);

  const nextExpiry=active?Math.min(observedUntil(active),canCompare?observedUntil(companion):Infinity):null;
  useEffect(()=>{
    if(nextExpiry===null)return;
    const timer=window.setTimeout(()=>setNow(Date.now()),Math.max(nextExpiry-Date.now(),1));
    return ()=>window.clearTimeout(timer);
  },[nextExpiry]);

  function showResult(observation:TransportObservation|null) {
    setManualPair(null);
    if(observation) {
      const kind=observation.result.provider==='didi'?'fare':'map';
      previous.current[kind]=observation;
    }
    setNow(Date.now());
    setCurrent(observation);
  }

  return <div className="hotel-transport">
    <div className="travel-launch">
      <button type="button" className="text-button" aria-expanded={open} onClick={()=>setOpen(value=>!value)}>
        {open?'收起交通':offline?'位置与返程':'查交通'}
      </button>
      {active&&parts.length>0&&<span className="hotel-transport-summary" role="status">
        {active.result.data_mode==='test'?'模拟 · ':''}{active.result.restaurant_name} → 酒店 · {parts.join(' · ')}{confirmed?' · 地点已人工核对':''}
      </span>}
    </div>
    {open&&manualReady&&<details className="transport-identity-review">
      <summary>核对两家来源的地点</summary>
      <p>分别核对两家来源的出发点和酒店。确认仅用于并列显示本次时间和估价。</p>
      {[active,companion].map(observation=><div key={observation.result.provider}>
        <strong>{sourceNames[observation.result.provider]}</strong>
        <p>出发点 · {observation.origin.name} · {observation.origin.city} · {observation.origin.address}</p>
        <p>酒店 · {observation.hotel.name} · {observation.hotel.city} · {observation.hotel.address}</p>
      </div>)}
      <button type="button" className="button outline" onClick={()=>{
        if(confirmed){setManualPair(null);return;}
        const checkedAt=Date.now();
        setNow(checkedAt);
        if(observedUntil(active)>checkedAt&&observedUntil(companion)>checkedAt){
          setManualPair({active,companion});
        }
      }}>{confirmed?'撤销地点核对':'确认两端各自是同一地点'}</button>
    </details>}
    {open&&<TravelPanel hotel={hotel} requestId={requestId} context={context} offline={offline} onResult={showResult}/>}
  </div>;
}
