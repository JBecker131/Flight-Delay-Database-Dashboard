/*
 * GET /api/departures?airport=CLT
 *
 * Today's and tomorrow's real departures from the live board, each scored by the delay model:
 *   { airport, status: 'ok' | 'quota_exhausted', model_status: 'ok' | 'partial' | 'unavailable',
 *     days, flights: [{ ...live row, prediction: {...} | null }] }
 */
import { predictRows } from '../lib/predictions.js';
import { config, getJson, json, UpstreamError } from '../lib/upstream.js';

const KEEP_DEPARTED_MIN = 10; // the board still shows flights that left a few minutes ago

export async function GET(request) {
  const code = (new URL(request.url).searchParams.get('airport') || '').trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(code)) return json({ error: 'airport must be a three-letter code, like CLT' }, { status: 400 });

  const cfg = config();
  let live;
  try {
    live = await getJson(`${cfg.liveUrl}/api/departures?airport=${code}`);
  } catch (error) {
    console.error(error);
    if (error instanceof UpstreamError && error.status === 404) return json({ error: `Unknown airport ${code}` }, { status: 404 });
    return json({ error: "Couldn't load departures" }, { status: 502 });
  }

  // No point scoring flights that have already gone.
  const cutoff = Date.now() - KEEP_DEPARTED_MIN * 60e3;
  const rows = (live.flights || [])
    .filter((r) => !r.dep_utc || Date.parse(r.dep_utc) >= cutoff)
    .map(({ origin_weather, ...rest }) => rest); // the page doesn't use it; the model reads weather itself

  const { predictions, modelStatus } = await predictRows(rows, code, cfg);
  const flights = rows.map((row, i) => ({ ...row, prediction: predictions[i] }));

  // A good answer can be shared for a few minutes; a partial one shouldn't stick.
  const complete = live.status === 'ok' && modelStatus === 'ok';
  return json(
    { airport: code, status: live.status, model_status: modelStatus, days: live.days, flights },
    { cache: complete ? 'public, max-age=60, s-maxage=300, stale-while-revalidate=300' : 'no-store' }
  );
}
