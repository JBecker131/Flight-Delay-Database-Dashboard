/*
 * Score departures with the delay model and turn its answer into what the board shows.
 *
 * The model predicts the *average* delay of flights like this one. It ranks flights well but can't
 * call the size of long delays (test RMSE is about 54 minutes), so anything it predicts over 59
 * minutes is shown as "60+" rather than a precise number that would usually be wrong.
 */
import { getJson, modelHeaders } from './upstream.js';

export const BATCH_SIZE = 500; // the most /predict takes in one request
export const DELAY_CAP_MIN = 60;

const AIRLINE = /^[A-Z0-9]{2,3}$/;
const AIRPORT = /^[A-Z]{3}$/;
const LOCAL_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

// BTS codes the model scores as, for "operated by" in the detail panel.
export const CARRIERS = {
  '9E': 'Endeavor Air', AA: 'American Airlines', AS: 'Alaska Airlines', B6: 'JetBlue', C5: 'CommuteAir',
  DL: 'Delta Air Lines', F9: 'Frontier Airlines', G4: 'Allegiant Air', G7: 'GoJet Airlines', HA: 'Hawaiian Airlines',
  MQ: 'Envoy Air', NK: 'Spirit Airlines', OH: 'PSA Airlines', OO: 'SkyWest Airlines', PT: 'Piedmont Airlines',
  QX: 'Horizon Air', SY: 'Sun Country', UA: 'United Airlines', WN: 'Southwest Airlines', YV: 'Mesa Airlines',
  YX: 'Republic Airways', ZW: 'Air Wisconsin',
};

/** "12" for 12.4 minutes, "60+" for anything over 59. */
export function delayDisplay(expectedMin) {
  const minutes = Math.max(0, Math.round(Number(expectedMin) || 0));
  return minutes >= DELAY_CAP_MIN
    ? { delayMin: DELAY_CAP_MIN, delayLabel: `${DELAY_CAP_MIN}+`, capped: true }
    : { delayMin: minutes, delayLabel: String(minutes), capped: false };
}

/** The model's request for one live-board row, or null if the row can't be scored. */
export function toModelFlight(row, origin) {
  const airline = (row.airline || '').trim().toUpperCase();
  const dest = (row.dest || '').trim().toUpperCase();
  if (!AIRLINE.test(airline) || !AIRPORT.test(origin) || !AIRPORT.test(dest) || origin === dest) return null;
  if (!LOCAL_TIME.test(row.dep_local || '')) return null;
  const flight = { airline, origin, dest, departure: row.dep_local.slice(0, 16) };
  if (row.flight_number) flight.flight_number = String(row.flight_number);
  // A bad arrival would fail the flight, so only send one that parses and comes after departure.
  if (LOCAL_TIME.test(row.arr_local || '') && (!row.arr_utc || !row.dep_utc || Date.parse(row.arr_utc) > Date.parse(row.dep_utc))) {
    flight.arrival = row.arr_local.slice(0, 16);
  }
  return flight;
}

/** One /predict result as the board's prediction, or null when the model couldn't score it. */
export function toPrediction(result) {
  if (!result || result.error || result.expected_delay_min == null) return null;
  const tier = { low: 'on-time', medium: 'possible', high: 'likely' }[result.risk] || 'possible';
  const percent = (share) => Math.round((Number(share) || 0) * 100);
  return {
    tier,
    // 1-99: how this flight ranks against the model's 411,465 test flights (88 = riskier than 88%).
    riskScore: Number.isInteger(result.risk_score) ? result.risk_score : null,
    probability: percent(result.chance_late_15), // share of similar test flights that left 15+ minutes late
    chanceLate60: percent(result.chance_late_60),
    ...delayDisplay(result.expected_delay_min),
    operatingAirline: result.operating_airline || null,
    operatorName: CARRIERS[result.operating_airline] || null,
    notes: result.notes || [],
  };
}

/**
 * Predictions for every row, in order (null where a row couldn't be scored), and how the model fared:
 * 'ok', 'partial' (some batches failed) or 'unavailable'.
 */
export async function predictRows(rows, origin, cfg, fetchImpl = fetch) {
  const predictions = rows.map(() => null);
  const requests = [];
  rows.forEach((row, i) => {
    const flight = toModelFlight(row, origin);
    if (flight) requests.push({ id: i, ...flight });
  });
  if (!requests.length) return { predictions, modelStatus: 'ok' };

  const batches = [];
  for (let i = 0; i < requests.length; i += BATCH_SIZE) batches.push(requests.slice(i, i + BATCH_SIZE));
  const outcomes = await Promise.allSettled(
    batches.map((flights) =>
      getJson(`${cfg.modelUrl}/predict`, { fetchImpl, method: 'POST', headers: modelHeaders(cfg), body: { flights } })
    )
  );

  let failed = 0;
  outcomes.forEach((outcome) => {
    if (outcome.status === 'rejected') {
      failed += 1;
      console.error('model batch failed:', outcome.reason?.message || outcome.reason);
      return;
    }
    for (const result of outcome.value.predictions || []) {
      if (Number.isInteger(result.id) && result.id >= 0 && result.id < rows.length) {
        predictions[result.id] = toPrediction(result);
      }
    }
  });
  const modelStatus = failed === 0 ? 'ok' : failed === batches.length ? 'unavailable' : 'partial';
  return { predictions, modelStatus };
}
