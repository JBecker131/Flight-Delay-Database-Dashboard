/*
 * Real departures and forecast weather from the live API (live/api.py), with placeholder
 * delay predictions until the model is trained.
 *
 * loadAirports()      -> [{ code, city, board, tz }]
 * loadDepartures(code) -> { status: 'ok' | 'quota_exhausted', flights: [flight] }
 *
 * {
 *   id:           'AA1423-2026-10-29',
 *   flightNumber: 'AA 1423',
 *   airline:      { code: 'AA', name: 'American Airlines' },
 *   origin, dest: { code: 'CLT', city: 'Charlotte', board: 'CHARLOTTE', tz: 'America/New_York' },
 *   departure:    Date,
 *   arrival:      Date | null,
 *   durationMin:  75 | null,
 *   destWeather: {            // forecast at the destination for the arrival hour, or null
 *     condition: 'Thunderstorms', icon: 'clear'|'cloud'|'rain'|'storm'|'wind'|'fog',
 *     tempF: 74, windMph: 18, windDir: 'SW' | null, visibilityMi: 2, rainChance: 80
 *   },
 *   gate:         'B12' | null,
 *   aircraft:     'Airbus A321' | null,
 *   cancelled:    false,      // already cancelled by the airline
 *   prediction: {
 *     probability:      0-100,
 *     tier:             'on-time' | 'possible' | 'likely',
 *     expectedDelayMin: 0,
 *     factors:          [{ name, detail, pts }]   // pts = percentage points
 *   }
 * }
 */
window.FlightData = (function () {
  // Placeholder: percentage points each airline adds to (or removes from) delay risk.
  const AIRLINE_RECORD = { AA: 4, DL: -6, UA: 2, WN: 3, B6: 9, AS: -4, NK: 8, F9: 6, G4: 5 };

  // WMO weather codes, as Open-Meteo reports them, grouped into what the board can show.
  const CONDITIONS = [
    { codes: [0, 1], label: 'Clear skies', icon: 'clear', pts: -4 },
    { codes: [2], label: 'Partly cloudy', icon: 'cloud', pts: 0 },
    { codes: [3], label: 'Overcast', icon: 'cloud', pts: 4 },
    { codes: [45, 48], label: 'Fog', icon: 'fog', pts: 12 },
    { codes: [51, 53, 55, 56, 57], label: 'Drizzle', icon: 'rain', pts: 4 },
    { codes: [61, 63, 80, 81], label: 'Rain', icon: 'rain', pts: 6 },
    { codes: [65, 66, 67, 82], label: 'Heavy rain', icon: 'rain', pts: 10 },
    { codes: [71, 73, 75, 77, 85, 86], label: 'Snow', icon: 'cloud', pts: 16 },
    { codes: [95, 96, 99], label: 'Thunderstorms', icon: 'storm', pts: 20 },
  ];
  const GUSTY = { label: 'Gusty winds', icon: 'wind', pts: 10 };
  const UNKNOWN = { label: 'No forecast yet', icon: 'cloud', pts: 0 };

  const WIND_DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const BASE_RISK = 25;

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
    const airports = await getJson('api/airports');
    airportsByCode = new Map(airports.map((a) => [a.code, a]));
    return airports;
  }

  function condition(code, windMph) {
    if (code == null) return UNKNOWN;
    const match = CONDITIONS.find((c) => c.codes.includes(code)) || UNKNOWN;
    return windMph >= 30 && match.pts < GUSTY.pts ? GUSTY : match;
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

  function tierFor(probability) {
    if (probability < 35) return 'on-time';
    if (probability < 60) return 'possible';
    return 'likely';
  }

  function localHour(date, tz) {
    return Number(date.toLocaleString('en-US', { hour: 'numeric', hourCycle: 'h23', timeZone: tz }));
  }

  // Placeholder scoring until the model is trained: real weather and schedule, made-up weights.
  function predict(flight, row, departuresThatDay) {
    const { origin, dest } = flight;
    const hour = localHour(flight.departure, origin.tz);
    const timeOfDay =
      hour < 9 ? { pts: -8, detail: 'Morning departures rarely inherit delays' }
      : hour < 15 ? { pts: 2, detail: 'Midday departure' }
      : hour < 19 ? { pts: 7, detail: 'Afternoon delays start to stack up' }
      : { pts: 12, detail: 'Evening flights inherit delays from earlier in the day' };
    const traffic =
      departuresThatDay >= 400 ? { pts: 5, label: 'Heavy traffic' }
      : departuresThatDay >= 150 ? { pts: 1, label: 'Busy day' }
      : { pts: -3, label: 'Light traffic' };
    const atOrigin = condition(row.origin_weather?.weather_code, row.origin_weather?.gust_max_mph);
    const atDest = condition(row.dest_weather?.weather_code, row.dest_weather?.wind_mph);

    const factors = [
      { name: 'Weather at departure', detail: `${atOrigin.label} in ${origin.city}`, pts: atOrigin.pts },
      { name: 'Weather at arrival', detail: `${atDest.label} in ${dest.city}`, pts: Math.round(atDest.pts * 0.6) },
      { name: 'Airport traffic', detail: `${traffic.label} at ${origin.code} (${departuresThatDay} departures)`, pts: traffic.pts },
      { name: 'Time of day', detail: timeOfDay.detail, pts: timeOfDay.pts },
      { name: 'Airline record', detail: `${flight.airline.name} on-time history`, pts: AIRLINE_RECORD[flight.airline.code] ?? 0 },
    ].sort((a, b) => Math.abs(b.pts) - Math.abs(a.pts));

    const probability = Math.max(3, Math.min(96, BASE_RISK + factors.reduce((sum, f) => sum + f.pts, 0)));
    const tier = tierFor(probability);
    const expectedDelayMin = tier === 'on-time' ? 0 : Math.round((10 + (probability - 35) * 1.1) / 5) * 5;
    return { probability, tier, expectedDelayMin, factors, baseRisk: BASE_RISK };
  }

  const utc = (s) => (s ? new Date(s) : null);

  async function loadDepartures(code) {
    const body = await getJson(`api/departures?airport=${encodeURIComponent(code)}`);
    const origin = airportsByCode.get(code);
    // Skip anything the board can't show rather than failing the whole airport.
    const rows = body.flights.filter((r) => r.dep_local && r.dep_utc && r.flight_number && airportsByCode.has(r.dest));
    const perDay = {};
    rows.forEach((r) => { perDay[r.dep_local.slice(0, 10)] = (perDay[r.dep_local.slice(0, 10)] || 0) + 1; });

    const flights = rows.map((r) => {
      const departure = utc(r.dep_utc);
      const arrival = utc(r.arr_utc);
      const flight = {
        id: `${r.flight_number.replace(' ', '')}-${r.dep_local}`,
        flightNumber: r.flight_number,
        airline: { code: r.airline || '', name: r.airline_name || r.airline || 'Unknown airline' },
        origin,
        dest: airportsByCode.get(r.dest),
        departure,
        arrival,
        durationMin: arrival ? Math.round((arrival - departure) / 60e3) : null,
        destWeather: destWeather(r.dest_weather),
        gate: r.gate,
        aircraft: r.aircraft,
        cancelled: /cancel/i.test(r.status || ''),
      };
      flight.prediction = predict(flight, r, perDay[r.dep_local.slice(0, 10)]);
      return flight;
    });
    return { status: body.status, flights };
  }

  return { TIERS, loadAirports, loadDepartures };
})();
