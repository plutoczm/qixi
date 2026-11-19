import {useGSAP} from '@gsap/react';
import gsap from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';
import type {RefObject} from 'react';

/** Progressive card reveal: deferred until a card scrolls into view. */
export default function useHotelListMotion(scope: RefObject<HTMLDivElement | null>, signature: string) {
  useGSAP((_context, contextSafe) => {
    const grid = scope.current;
    if (!grid || typeof window.matchMedia !== 'function' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    gsap.registerPlugin(ScrollTrigger, useGSAP);
    const cards = Array.from(grid.querySelectorAll<HTMLElement>('.recall-hotel-group'));
    if (cards.length===0) return;
    const triggers = ScrollTrigger.batch(cards, {
      start: 'top 94%', once: true, interval: .07, batchMax: 3,
      onEnter: contextSafe!(group => gsap.fromTo(group, {autoAlpha: 0, y: 27},
        {autoAlpha: 1, y: 0, duration: .63, stagger: .075, ease: 'power3.out',
          clearProps: 'transform,opacity,visibility'})),
    });
    return () => triggers.forEach(trigger => trigger.kill());
  }, {scope, dependencies: [signature], revertOnUpdate: true});
}
