/** Presentational placeholders only; never suggest that any hotel or price has been found. */
export default function HotelSearchSkeleton() {
  return <div className="hotel-search-skeletons" aria-hidden="true">
    {[0, 1].map(index => <article className="hotel-search-skeleton" data-testid="search-preview-skeleton" key={index}>
      <div className="skeleton-image skeleton-glimmer"/>
      <div className="skeleton-copy">
        <span className="skeleton-small skeleton-glimmer"/>
        <span className="skeleton-title skeleton-glimmer"/>
        <span className="skeleton-subtitle skeleton-glimmer"/>
        <div className="skeleton-tags"><span className="skeleton-glimmer"/><span className="skeleton-glimmer"/></div>
        <span className="skeleton-foot skeleton-glimmer"/>
      </div>
    </article>)}
  </div>;
}
