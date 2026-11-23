// GET /api/airports -> [{ code, city, board, tz }], every airport the live board knows.
import { config, getJson, json } from '../lib/upstream.js';

export async function GET() {
  try {
    const airports = await getJson(`${config().liveUrl}/api/airports`);
    // The list only changes when the BTS data does, so let Vercel's CDN keep it for a day.
    return json(airports, { cache: 'public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400' });
  } catch (error) {
    console.error(error);
    return json({ error: "Couldn't load the airport list" }, { status: 502 });
  }
}
