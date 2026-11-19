import {useLayoutEffect, useRef} from 'react';
import {useGSAP} from '@gsap/react';
import gsap from 'gsap';
import {Flip} from 'gsap/Flip';
import type {Hotel} from './types';

export default function ComparisonDock({items,onView,onClear,onRemove}:{
  items: readonly Hotel[];
  onView: ()=>void;
  onClear: ()=>void;
  onRemove: (id:number)=>void;
}) {
  const root=useRef<HTMLDivElement>(null);
  const lastState=useRef<ReturnType<typeof Flip.getState> | null>(null);
  const activeFlip=useRef<gsap.core.Animation | null>(null);
  const ids=items.map(item=>item.id).join(',');
  const motionAllowed=typeof window !== 'undefined' && typeof window.matchMedia==='function' &&
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  useGSAP(() => {
    if (!motionAllowed || !root.current) return;
    gsap.registerPlugin(Flip, useGSAP);
    gsap.fromTo(root.current,{autoAlpha:0,y:23,scale:.98},
      {autoAlpha:1,y:0,scale:1,duration:.36,ease:'power3.out',clearProps:'transform,opacity,visibility'});
  }, {scope: root});

  useLayoutEffect(() => {
    if (!motionAllowed || !root.current) return;
    gsap.registerPlugin(Flip);
    const chips=Array.from(root.current.querySelectorAll<HTMLElement>('[data-dock-chip]'));
    const remember=()=>{lastState.current=Flip.getState(chips);};
    if (lastState.current) {
      activeFlip.current?.kill();
      activeFlip.current=Flip.from(lastState.current,{
        targets:chips,
        duration:.26,ease:'power2.out',
        onEnter:entered=>gsap.fromTo(entered,{opacity:0,y:8},
          {opacity:1,y:0,duration:.22,ease:'power2.out'}),
        onComplete:remember,
      });
    } else {
      remember();
    }
    return () => {activeFlip.current?.kill();};
  }, [ids, motionAllowed]);

  return <div className="comparison-dock" role="region" aria-label="酒店对比快捷栏" ref={root}>
    <div className="dock-heading">
      <span className="dock-glyph" aria-hidden="true">≋</span>
      <div><strong>酒店对比</strong><small>{items.length} / 3</small></div>
    </div>
    <div className="dock-chips" aria-label="已选择的酒店">
      {items.map(hotel=><span className="dock-chip" data-dock-chip key={hotel.id}>
        <span title={hotel.name}>{hotel.name}</span>
        <button type="button" onClick={()=>onRemove(hotel.id)} aria-label={'移出快捷对比：'+hotel.name}>×</button>
      </span>)}
    </div>
    <div className="dock-actions">
      <button className="dock-clear" type="button" onClick={onClear}>清空选择</button>
      <button className="dock-view" type="button" disabled={items.length<2} onClick={onView}>查看对比 <span aria-hidden="true">↗</span></button>
    </div>
  </div>;
}
