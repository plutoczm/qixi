import {useGSAP} from '@gsap/react';
import gsap from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';
import type {RefObject} from 'react';

/**
 * Decorative motion only. Never initiates searches, changes data or blocks keyboard input.
 * Animations are scoped and reverted on unmount (including React StrictMode).
 */
export default function useCinematicMotion(scope: RefObject<HTMLDivElement | null>) {
  useGSAP((_context, contextSafe) => {
    const root = scope.current;
    if (!root || typeof window.matchMedia !== 'function' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    // ScrollTrigger registration touches matchMedia; defer it until a visual browser is ready.
    gsap.registerPlugin(ScrollTrigger, useGSAP);
    const select = gsap.utils.selector(root);
    const intro = gsap.timeline({defaults: {ease: 'power3.out'}});
    intro
      .fromTo(select('.hero-copy > *'), {autoAlpha: 0, y: 21},
        {autoAlpha: 1, y: 0, duration: .72, stagger: .075}, 0)
      .fromTo(select('.hero-scene'), {autoAlpha: 0, y: 16, scale: .975},
        {autoAlpha: 1, y: 0, scale: 1, duration: .95}, .14)
      .fromTo(select('.scene-index, .scene-seal'), {autoAlpha: 0, y: 12},
        {autoAlpha: 1, y: 0, duration: .48, stagger: .12}, .45)
      .fromTo(select('.scene-note'), {autoAlpha: 0, y: 18, rotation: -1},
        {autoAlpha: 1, y: 0, rotation: 0, duration: .68}, .55)
      .fromTo(select('.booking-zone > .search-form'), {autoAlpha: 0, y: 19},
        {autoAlpha: 1, y: 0, duration: .65}, .28)
      .fromTo(select('.assistant-drawer'), {autoAlpha: 0, y: 14},
        {autoAlpha: 1, y: 0, duration: .54}, .58);

    const header = select('.site-header')[0] as HTMLElement | undefined;
    if (header) {
      ScrollTrigger.create({
        trigger: root, start: 'top -64px', end: 'bottom top',
        onToggle: self => header.classList.toggle('is-scrolled', self.isActive),
      });
    }

    // Content stays visible until entering the viewport; no permanent hidden-on-load state.
    for (const element of select('[data-scroll-reveal]') as HTMLElement[]) {
      ScrollTrigger.create({
        trigger: element, start: 'top 91%', once: true,
        onEnter: contextSafe!(() => gsap.fromTo(element, {autoAlpha: 0, y: 22},
          {autoAlpha: 1, y: 0, duration: .68, ease: 'power3.out', clearProps: 'transform,opacity,visibility'})),
      });
    }

    const media = gsap.matchMedia();
    media.add('(min-width: 900px) and (prefers-reduced-motion: no-preference)', () => {
      const scene = select('.hero-scene')[0] as HTMLElement | undefined;
      const artwork = select('.scene-picture img')[0] as HTMLElement | undefined;
      const atmosphere = select('.scene-atmosphere')[0] as HTMLElement | undefined;
      if (!scene || !artwork || !atmosphere) return;
      gsap.to(artwork, {yPercent: -7, scale: 1.11, ease: 'none',
        scrollTrigger: {trigger: scene, start: 'top 16%', end: 'bottom top', scrub: .75}});
      gsap.to(atmosphere, {yPercent: -10, ease: 'none',
        scrollTrigger: {trigger: scene, start: 'top 16%', end: 'bottom top', scrub: .75}});
    });

    const scene = select('.hero-scene')[0] as HTMLElement | undefined;
    const atmosphere = select('.scene-atmosphere')[0] as HTMLElement | undefined;
    if (scene && atmosphere && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      const moveX = gsap.quickTo(atmosphere, 'x', {duration: .55, ease: 'power2.out'});
      const moveY = gsap.quickTo(atmosphere, 'y', {duration: .55, ease: 'power2.out'});
      const onMove = (event: PointerEvent) => {
        const box = scene.getBoundingClientRect();
        moveX(((event.clientX - box.left) / box.width - .5) * 14);
        moveY(((event.clientY - box.top) / box.height - .5) * 11);
      };
      const onLeave = () => {moveX(0); moveY(0);};
      scene.addEventListener('pointermove', onMove);
      scene.addEventListener('pointerleave', onLeave);
      return () => {
        scene.removeEventListener('pointermove', onMove);
        scene.removeEventListener('pointerleave', onLeave);
        media.revert();
        header?.classList.remove('is-scrolled');
      };
    }
    return () => {media.revert(); header?.classList.remove('is-scrolled');};
  }, {scope});
}
