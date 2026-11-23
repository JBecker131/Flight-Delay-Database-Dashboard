/*
 * GET /api/explain?airline=AA&flight_number=AA%205069&origin=CLT&dest=DFW&departure=2026-11-23T17:05&arrival=...
 *
 * What the model looked at for one flight, in plain words: { items: [{ name, detail }], notes }.
 * Loaded when someone opens a flight, so the board itself stays light.
 */
import { explain } from '../lib/explain.js';
import { toModelFlight } from '../lib/predictions.js';
import { config, getJson, json, modelHeaders } from '../lib/upstream.js';

export async function GET(request) {
  const q = new URL(request.url).searchParams;
  const origin = (q.get('origin') || '').trim().toUpperCase();
  const flight = toModelFlight(
    { airline: q.get('airline'), flight_number: q.get('flight_number'), dest: q.get('dest'),
      dep_local: q.get('departure'), arr_local: q.get('arrival') },
    origin
  );
  if (!flight) return json({ error: 'Need airline, origin, dest and departure (local time, like 2026-11-23T17:05)' }, { status: 400 });

  const cfg = config();
  try {
    const body = await getJson(`${cfg.modelUrl}/predict`, {
      method: 'POST',
      headers: modelHeaders(cfg),
      body: { flights: [flight], include_features: true },
    });
    const result = body.predictions?.[0];
    if (!result || result.error) return json({ error: result?.error || 'The model could not score this flight' }, { status: 422 });
    return json(
      { items: explain(result, flight), notes: result.notes || [] },
      { cache: 'public, max-age=300, s-maxage=900, stale-while-revalidate=900' }
    );
  } catch (error) {
    console.error(error);
    return json({ error: "Couldn't reach the delay model" }, { status: 502 });
  }
}
