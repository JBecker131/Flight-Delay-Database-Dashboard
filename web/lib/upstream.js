/*
 * The two Modal services this site sits in front of.
 *
 *   LIVE_API_URL     the live departures board (live/api.py): real AeroDataBox schedules + Open-Meteo forecast
 *   MODEL_API_URL    the delay model (model/api.py): POST /predict, keyed with PREDICT_API_KEY
 *   PREDICT_API_KEY  sent as "Authorization: Bearer <key>". Only ever used here, on the server,
 *                    so it never reaches the browser.
 */
const DEFAULT_LIVE = 'https://jbecke20--flight-departures-board.us-east.modal.run';
const DEFAULT_MODEL = 'https://jbecke20--flight-delay-predict.us-east.modal.run';

// A cold Modal container reads its lookups before answering, so allow it a while.
const TIMEOUT_MS = 60_000;

export class UpstreamError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export function config(env = process.env) {
  const trim = (url) => url.replace(/\/+$/, '');
  return {
    liveUrl: trim(env.LIVE_API_URL || DEFAULT_LIVE),
    modelUrl: trim(env.MODEL_API_URL || DEFAULT_MODEL),
    apiKey: env.PREDICT_API_KEY || '',
  };
}

export async function getJson(url, { fetchImpl = fetch, headers = {}, method = 'GET', body } = {}) {
  const response = await fetchImpl(url, {
    method,
    headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new UpstreamError(`${method} ${url.split('?')[0]}: ${response.status} ${detail.slice(0, 200)}`, response.status);
  }
  return response.json();
}

export function modelHeaders(cfg) {
  return cfg.apiKey ? { Authorization: `Bearer ${cfg.apiKey}` } : {};
}

export function json(data, { status = 200, cache = 'no-store' } = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': cache },
  });
}
