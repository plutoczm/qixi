import {addDays, chinaDate} from './format';
import type {PlaceType, SearchInput} from './types';

// Only a user's manually saved form conditions live in this tab's session.
// Results, rates, API keys, URLs and map selections are never persisted.
const STORAGE_KEY = 'xunqi:recent-search:v1';
const PLACE_TYPES: ReadonlyArray<PlaceType> = ['城市','区/县','景点','地铁站','酒店','详细地址','火车站','机场'];

function validDay(day: unknown): day is string {
  if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
  const date = new Date(day + 'T00:00:00Z');
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === day;
}

function validated(data: unknown): SearchInput | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const item = data as Record<string, unknown>;
  const {place,place_type,check_in,check_out,adults,rooms,children_ages,budget_fen,limit,poi_name,accepts_dormitory,country_region,waypoints} = item;
  if (typeof place !== 'string' || place.trim().length === 0 || place.length > 120 ||
    !PLACE_TYPES.includes(place_type as PlaceType) || !validDay(check_in) || !validDay(check_out) ||
    check_in < chinaDate() || check_out <= check_in || check_out > addDays(check_in,28) ||
    !Number.isInteger(adults) || (adults as number) < 1 || (adults as number) > 8 ||
    !Number.isInteger(rooms) || (rooms as number) < 1 || (rooms as number) > 4 ||
    !Array.isArray(children_ages) || children_ages.length > 4 ||
    !children_ages.every(age => Number.isInteger(age) && age >= 0 && age <= 17) ||
    (budget_fen !== null && (!Number.isSafeInteger(budget_fen) || (budget_fen as number) < 0 || (budget_fen as number) > 100000000)) ||
    ![6,10,20].includes(limit as number) || poi_name !== null ||
    (country_region !== undefined && country_region !== null &&
      (typeof country_region !== 'string' || country_region.length > 80 ||
       /[\u0000-\u001f\u007f]/.test(country_region) ||
       (country_region.trim()!=='' && !['中国','中国大陆','中华人民共和国','china','mainland china','prc'].includes(country_region.trim().toLocaleLowerCase())))) ||
    (waypoints !== undefined && (!Array.isArray(waypoints) || waypoints.length > 5 ||
      !waypoints.every(stop=>typeof stop==='string' && stop.trim().length>0 && stop.length<=120 && !/[\u0000-\u001f\u007f]/.test(stop)) ||
      new Set(waypoints.map(stop=>(stop as string).trim().toLowerCase())).size!==waypoints.length)) ||
    (accepts_dormitory !== undefined && typeof accepts_dormitory !== 'boolean')) return null;
  // Explicit allow-list prevents accidental storage of credentials or supplier responses.
  return {
    place: place.trim(),place_type:place_type as PlaceType,check_in,check_out,
    adults:adults as number,rooms:rooms as number,
    children_ages:[...(children_ages as number[])],budget_fen:budget_fen as number|null,
    limit:limit as number,poi_name:null,country_region:typeof country_region==='string'?country_region.trim()||null:null,
    waypoints:Array.isArray(waypoints)?waypoints.map(stop=>(stop as string).trim()):[],
    accepts_dormitory:accepts_dormitory === true,
  };
}

export function saveRecentSearch(input: SearchInput): boolean {
  const clean = validated(input);
  if (!clean) return false;
  try {
    window.sessionStorage.setItem(STORAGE_KEY,JSON.stringify({version:1,input:clean}));
    return true;
  } catch { return false; }
}

export function readRecentSearch(): SearchInput|null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw || raw.length > 4096) return null;
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const record = data as {version?:unknown;input?:unknown};
    if (record.version !== 1) return null;
    return validated(record.input);
  } catch { return null; }
}

export function clearRecentSearch(): void {
  try {window.sessionStorage.removeItem(STORAGE_KEY);} catch {/* storage unavailable */}
}
