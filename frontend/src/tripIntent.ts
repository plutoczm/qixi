/** LLM-recognized plans are user intentions, not verified map POIs. */
import type {PlannedVisit} from './types';

export type TripHandoff = {
  visits:PlannedVisit[];
  /** Legacy opt-in restaurant-specific map workflow; not the general path. */
  restaurantAnchor:string|null;
  /** User-chosen hotel search anchor, of any visit category. */
  stayAnchor?:string|null;
};

export function restaurantChoices(visits:readonly PlannedVisit[]):PlannedVisit[]{
  return visits.filter(visit=>visit.category==='餐饮');
}

/** Only an explicitly selected restaurant can enter the legacy recall workflow. */
export function selectedRestaurant(visits:readonly PlannedVisit[],name:string|null):string|null{
  return name&&restaurantChoices(visits).some(visit=>visit.name===name)?name:null;
}

/** No LLM location becomes a hotel search anchor without user selection. */
export function selectedVisit(visits:readonly PlannedVisit[],name:string|null):PlannedVisit|null{
  return visits.find(visit=>name&&visit.name===name)??null;
}
