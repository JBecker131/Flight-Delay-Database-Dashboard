/*
 * The model's inputs for one flight, in plain words, for "What the model looked at".
 *
 * Gradient boosting combines its inputs in ways that don't split into "+3 points for weather",
 * so this lists what the model saw rather than claiming how much each part moved the result.
 */
import { CARRIERS } from './predictions.js';

const CONDITIONS = [
  [[0, 1], 'Clear'], [[2], 'Partly cloudy'], [[3], 'Overcast'], [[45, 48], 'Fog'],
  [[51, 53, 55, 56, 57], 'Drizzle'], [[61, 63, 80, 81], 'Rain'], [[65, 66, 67, 82], 'Heavy rain'],
  [[71, 73, 75, 77, 85, 86], 'Snow'], [[95, 96, 99], 'Thunderstorms'],
];
const DAYS = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const pct = (share) => `${Math.round(share * 100)}%`;
const carrierName = (code) => CARRIERS[code] || code;

function hourLabel(hour) {
  const h = hour % 12 || 12;
  return `${h} ${hour < 12 ? 'AM' : 'PM'}`;
}

export function weatherText(f, side) {
  const code = f[`${side}_weather_code`];
  if (!isNum(code)) return 'No forecast yet, so the model scored it without weather';
  const label = (CONDITIONS.find(([codes]) => codes.includes(code)) || [null, 'Mixed conditions'])[1];
  const snow = f[`${side}_snowfall_in`];
  const precip = f[`${side}_precip_in`];
  const parts = [
    isNum(snow) && snow >= 0.1 ? `${label} (${snow.toFixed(1)} in of snow)`
      : isNum(precip) && precip >= 0.05 ? `${label} (${precip.toFixed(2)} in of precipitation)`
      : label,
  ];
  if (isNum(f[`${side}_gust_max_mph`])) parts.push(`gusts to ${Math.round(f[`${side}_gust_max_mph`])} mph`);
  if (isNum(f[`${side}_temp_max_f`]) && isNum(f[`${side}_temp_min_f`])) {
    parts.push(`${Math.round(f[`${side}_temp_min_f`])}–${Math.round(f[`${side}_temp_max_f`])}°F`);
  }
  return parts.join(', ');
}

function recordText(f, side, carrier, airport) {
  const late = f[`${side}_prev_del15_rate`];
  if (!isNum(late)) return null;
  let text = `${pct(late)} of ${carrierName(carrier)} flights into ${airport} arrived 15+ minutes late`;
  const lateAircraft = f[`${side}_prev_late_aircraft_rate`];
  if (isNum(lateAircraft) && lateAircraft > 0) text += `; ${pct(lateAircraft)} were held up by a late incoming plane`;
  return text;
}

/** [{ name, detail }] from a /predict result made with include_features. */
export function explain(result, { airline, origin, dest }) {
  const f = result.features || {};
  const carrier = result.operating_airline || airline;
  const items = [];

  items.push({
    name: 'Who flies it',
    detail: carrier !== airline
      ? `Operated by ${carrierName(carrier)} for ${carrierName(airline)}, so it's scored on the regional's own record`
      : `${carrierName(carrier)} operates this flight itself`,
  });

  if (isNum(f.dep_hour)) {
    const day = DAYS[f.day_of_week] || '';
    items.push({
      name: 'When it leaves',
      detail: `${day ? `${day}, ` : ''}${hourLabel(f.dep_hour)} hour. ${
        f.dep_hour < 9 ? 'Early departures rarely inherit delays.' : 'Later flights can inherit delays from earlier in the day.'
      }`,
    });
  }

  items.push({ name: `Weather at ${origin}`, detail: weatherText(f, 'origin') });
  items.push({ name: `Weather at ${dest}`, detail: weatherText(f, 'dest') });

  const atOrigin = recordText(f, 'origin', carrier, origin);
  const atDest = recordText(f, 'dest', carrier, dest);
  if (atOrigin) items.push({ name: `Airline record at ${origin}`, detail: `Latest BTS data for this time of year: ${atOrigin}` });
  if (atDest) items.push({ name: `Airline record at ${dest}`, detail: `Latest BTS data for this time of year: ${atDest}` });

  if (isNum(f.origin_prev_hour_dep_ontime_pct)) {
    items.push({
      name: `${origin} at this hour`,
      detail: `${Math.round(f.origin_prev_hour_dep_ontime_pct)}% of departures in this hour left on time, per DOT's consumer reports`,
    });
  }

  if (isNum(f.origin_hour_flights)) {
    const day = isNum(f.origin_day_flights) ? ` and about ${Math.round(f.origin_day_flights)} that day` : '';
    items.push({
      name: `How busy ${origin} is`,
      detail: `Typically about ${Math.round(f.origin_hour_flights)} departures scheduled in this hour${day}`,
    });
  }
  return items;
}
