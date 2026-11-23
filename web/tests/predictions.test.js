import assert from 'node:assert/strict';
import { test } from 'node:test';

import { explain } from '../lib/explain.js';
import { BATCH_SIZE, delayDisplay, predictRows, toModelFlight, toPrediction } from '../lib/predictions.js';
import { modelHeaders } from '../lib/upstream.js';

const row = (over = {}) => ({
  airline: 'AA', flight_number: 'AA 5069', dest: 'DFW',
  dep_local: '2026-11-23T17:05', dep_utc: '2026-11-23T22:05Z',
  arr_local: '2026-11-23T19:20', arr_utc: '2026-11-24T01:20Z', ...over,
});

const result = (id, over = {}) => ({
  id, expected_delay_min: 22.8, risk: 'high', risk_score: 84, chance_late_15: 0.3238, chance_late_60: 0.1102,
  operating_airline: 'OH', notes: ['AA 5069 from CLT is operated by OH'], error: null, ...over,
});

test('delays over 59 minutes show as 60+', () => {
  assert.deepEqual(delayDisplay(59.4), { delayMin: 59, delayLabel: '59', capped: false });
  assert.deepEqual(delayDisplay(59.6), { delayMin: 60, delayLabel: '60+', capped: true });
  assert.deepEqual(delayDisplay(143), { delayMin: 60, delayLabel: '60+', capped: true });
  assert.deepEqual(delayDisplay(-2), { delayMin: 0, delayLabel: '0', capped: false });
});

test('a prediction keeps no exact minutes past the cap', () => {
  const p = toPrediction(result(0, { expected_delay_min: 97.3 }));
  assert.equal(p.delayLabel, '60+');
  assert.equal(p.delayMin, 60);
  assert.ok(!JSON.stringify(p).includes('97'));
});

test('model risk maps onto the board tiers and percentages', () => {
  const p = toPrediction(result(0));
  assert.equal(p.tier, 'likely');
  assert.equal(p.riskScore, 84);
  assert.equal(p.probability, 32);
  assert.equal(toPrediction(result(0, { risk_score: undefined })).riskScore, null);
  assert.equal(p.chanceLate60, 11);
  assert.equal(p.delayLabel, '23');
  assert.equal(p.operatorName, 'PSA Airlines');
  assert.equal(toPrediction(result(0, { risk: 'low' })).tier, 'on-time');
  assert.equal(toPrediction(result(0, { risk: 'medium' })).tier, 'possible');
  assert.equal(toPrediction(result(0, { error: 'unknown airport XYZ', expected_delay_min: null })), null);
});

test('rows that would fail validation are not sent to the model', () => {
  assert.deepEqual(toModelFlight(row(), 'CLT'), {
    airline: 'AA', origin: 'CLT', dest: 'DFW', departure: '2026-11-23T17:05',
    flight_number: 'AA 5069', arrival: '2026-11-23T19:20',
  });
  assert.equal(toModelFlight(row({ airline: null }), 'CLT'), null);
  assert.equal(toModelFlight(row({ dest: 'CLT' }), 'CLT'), null);
  assert.equal(toModelFlight(row({ dep_local: 'soon' }), 'CLT'), null);
  // An arrival before departure is dropped rather than failing the flight.
  assert.equal(toModelFlight(row({ arr_utc: '2026-11-23T20:00Z' }), 'CLT').arrival, undefined);
});

test('flights are scored in batches and matched back by id', async () => {
  const rows = Array.from({ length: BATCH_SIZE + 3 }, () => row());
  rows[1] = row({ airline: null }); // unscoreable
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const { flights } = JSON.parse(init.body);
    return Response.json({ predictions: flights.map((f) => result(f.id, { expected_delay_min: f.id })) });
  };
  const cfg = { modelUrl: 'https://model.test', apiKey: 'k' };
  const { predictions, modelStatus } = await predictRows(rows, 'CLT', cfg, fetchImpl);
  assert.equal(modelStatus, 'ok');
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, 'https://model.test/predict');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer k');
  assert.equal(predictions[1], null);
  assert.equal(predictions[2].delayMin, 2);
  assert.equal(predictions[BATCH_SIZE + 2].delayLabel, '60+');
});

test('a failing model leaves the board up without forecasts', async () => {
  const fetchImpl = async () => new Response('boom', { status: 500 });
  const { predictions, modelStatus } = await predictRows([row(), row()], 'CLT', { modelUrl: 'https://m' }, fetchImpl);
  assert.equal(modelStatus, 'unavailable');
  assert.deepEqual(predictions, [null, null]);
});

test('no key means no Authorization header', () => {
  assert.deepEqual(modelHeaders({ apiKey: '' }), {});
});

test('explanations describe the inputs in plain words', () => {
  const items = explain(
    {
      operating_airline: 'OH',
      features: {
        day_of_week: 1, dep_hour: 17, origin_weather_code: 63, origin_precip_in: 0.114, origin_gust_max_mph: 22.4,
        origin_temp_min_f: 50.5, origin_temp_max_f: 73.1, dest_weather_code: null,
        origin_prev_del15_rate: 0.109, origin_prev_late_aircraft_rate: 0.051, origin_prev_hour_dep_ontime_pct: 74.9,
        origin_hour_flights: 43.5, origin_day_flights: 497.25,
      },
    },
    { airline: 'AA', origin: 'CLT', dest: 'DFW' }
  );
  const byName = Object.fromEntries(items.map((i) => [i.name, i.detail]));
  assert.match(byName['Who flies it'], /PSA Airlines for American Airlines/);
  assert.match(byName['When it leaves'], /Monday, 5 PM hour/);
  assert.equal(byName['Weather at CLT'], 'Rain (0.11 in of precipitation), gusts to 22 mph, 51–73°F');
  assert.match(byName['Weather at DFW'], /No forecast yet/);
  assert.match(byName['Airline record at CLT'], /11% of PSA Airlines flights into CLT/);
  assert.match(byName['CLT at this hour'], /75% of departures/);
  assert.match(byName['How busy CLT is'], /about 44 departures .* about 497 that day/);
});
