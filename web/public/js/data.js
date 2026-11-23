/*
 * Real departures and forecast weather, scored by the gradient boosting delay model on the server
 * (api/departures.js). The browser never talks to the model directly, so its API key stays secret.
 *
 * loadAirports()       -> [{ code, city, board, tz }]
 * loadDepartures(code) -> { status: 'ok' | 'quota_exhausted', modelStatus: 'ok' | 'partial' | 'unavailable', flights }
 * loadExplanation(f)   -> { items: [{ name, detail }], notes: [string] }
 *
 * {
 *   id:           'AA1423-2026-10-29T09:05',
 *   flightNumber: 'AA 1423',
 *   airline:      { code: 'AA', name: 'American Airlines' },
 *   origin, dest: { code: 'CLT', city: 'Charlotte', board: 'CHARLOTTE', tz: 'America/New_York' },
 *   departure:    Date,           depLocal: '2026-10-29T09:05'
 *   arrival:      Date | null,    arrLocal: '2026-10-29T10:20' | null
 *   durationMin:  75 | null,
 *   destWeather:  { condition, icon, tempF, windMph, windDir, visibilityMi, rainChance } | null,
 *   aircraft, cancelled,
 *   prediction: {                 // null when the model couldn't score the flight
 *     tier:          'on-time' | 'possible' | 'likely',   // the model's low / medium / high risk
 *     probability:   0-100,       // share of similar test flights that left 15+ minutes late
 *     chanceLate60:  0-100,
 *     delayMin:      0-60,        // expected delay, capped at 60
 *     delayLabel:    '12' | '60+',
 *     capped:        true when the model predicted more than 59 minutes,
 *     operatingAirline, operatorName, notes
 *   }
 * }
 */
window.FlightData = (function () {
  // WMO weather codes, as Open-Meteo reports them, grouped into what the board can show.
  const CONDITIONS = [
    { codes: [0, 1], label: 'Clear skies', icon: 'clear' },
    { codes: [2], label: 'Partly cloudy', icon: 'cloud' },
    { codes: [3], label: 'Overcast', icon: 'cloud' },
    { codes: [45, 48], label: 'Fog', icon: 'fog' },
    { codes: [51, 53, 55, 56, 57], label: 'Drizzle', icon: 'rain' },
    { codes: [61, 63, 80, 81], label: 'Rain', icon: 'rain' },
    { codes: [65, 66, 67, 82], label: 'Heavy rain', icon: 'rain' },
    { codes: [71, 73, 75, 77, 85, 86], label: 'Snow', icon: 'cloud' },
    { codes: [95, 96, 99], label: 'Thunderstorms', icon: 'storm' },
  ];
  const GUSTY = { label: 'Gusty winds', icon: 'wind' };
  const UNKNOWN = { label: 'No forecast yet', icon: 'cloud' };
  const CALM_WEATHER = new Set(['Clear skies', 'Partly cloudy', 'Overcast']);
  const WIND_DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

  const TIERS = {
    'on-time': { board: 'ON TIME', label: 'Likely on time', tone: 'ok' },
    possible: { board: 'POSSIBLE DELAY', label: 'Could be delayed', tone: 'warn' },
    likely: { board: 'LIKELY DELAYED', label: 'Likely delayed', tone: 'bad' },
  };

  let airportsByCode = new Map();

  async function getJson(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    return response.json();
  }

  async function loadAirports() {
    const airports = await getJson('/api/airports');
    airportsByCode = new Map(airports.map((a) => [a.code, a]));
    return airports;
  }

  function condition(code, windMph) {
    if (code == null) return UNKNOWN;
    const match = CONDITIONS.find((c) => c.codes.includes(code)) || UNKNOWN;
    return windMph >= 30 && CALM_WEATHER.has(match.label) ? GUSTY : match;
  }

  function destWeather(wx) {
    if (!wx || wx.weather_code == null || wx.temp_f == null) return null;
    const c = condition(wx.weather_code, wx.wind_mph);
    const windMph = Math.round(wx.wind_mph ?? 0);
    return {
      condition: c.label,
      icon: c.icon,
      tempF: Math.round(wx.temp_f),
      windMph,
      windDir: windMph >= 3 && wx.wind_dir_deg != null ? WIND_DIRECTIONS[Math.round(wx.wind_dir_deg / 45) % 8] : null,
      visibilityMi: Math.round(Math.min(wx.visibility_mi ?? 10, 10) * 10) / 10,
      rainChance: Math.round(wx.precip_prob ?? 0),
    };
  }

  const utc = (s) => (s ? new Date(s) : null);

  async function loadDepartures(code) {
    const body = await getJson(`/api/departures?airport=${encodeURIComponent(code)}`);
    const origin = airportsByCode.get(code);
    // Skip anything the board can't show rather than failing the whole airport.
    const rows = body.flights.filter((r) => r.dep_local && r.dep_utc && r.flight_number && airportsByCode.has(r.dest));

    const flights = rows.map((r) => {
      const departure = utc(r.dep_utc);
      const arrival = utc(r.arr_utc);
      return {
        id: `${r.flight_number.replace(' ', '')}-${r.dep_local}`,
        flightNumber: r.flight_number,
        airline: { code: r.airline || '', name: r.airline_name || r.airline || 'Unknown airline' },
        origin,
        dest: airportsByCode.get(r.dest),
        departure,
        arrival,
        depLocal: r.dep_local,
        arrLocal: r.arr_local || null,
        durationMin: arrival ? Math.round((arrival - departure) / 60e3) : null,
        destWeather: destWeather(r.dest_weather),
        aircraft: r.aircraft,
        cancelled: /cancel/i.test(r.status || ''),
        prediction: r.prediction || null,
      };
    });
    return { status: body.status, modelStatus: body.model_status, flights };
  }

  const explanations = new Map();

  function loadExplanation(f) {
    if (!explanations.has(f.id)) {
      const q = new URLSearchParams({
        airline: f.airline.code, flight_number: f.flightNumber, origin: f.origin.code, dest: f.dest.code, departure: f.depLocal,
      });
      if (f.arrLocal) q.set('arrival', f.arrLocal);
      const pending = getJson(`/api/explain?${q}`);
      pending.catch(() => explanations.delete(f.id)); // let a failed lookup be retried
      explanations.set(f.id, pending);
    }
    return explanations.get(f.id);
  }

  return { TIERS, loadAirports, loadDepartures, loadExplanation };
})();
