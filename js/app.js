(function () {
  const { TIERS, loadAirports, loadDepartures } = window.FlightData;
  const SplitFlap = window.SplitFlap;

  const WINDOW_BEFORE_MIN = 10; // keep flights that left a few minutes ago
  const WINDOW_AFTER_HOURS = 16;
  const narrowQuery = window.matchMedia('(max-width: 720px)');

  const $ = (id) => document.getElementById(id);
  const els = {
    board: $('board'),
    head: $('board-head'),
    rows: $('board-rows'),
    status: $('board-status'),
    origin: $('filter-origin'),
    dest: $('filter-dest'),
    flight: $('filter-flight'),
    clear: $('clear-filters'),
    prev: $('page-prev'),
    next: $('page-next'),
    pageLabel: $('page-label'),
    clockTime: $('clock-time'),
    clockDate: $('clock-date'),
    dialog: $('flight-dialog'),
    detailTitle: $('detail-title'),
    detailRoute: $('detail-route'),
    detailRisk: $('detail-risk'),
    detailVerdict: $('detail-verdict'),
    detailFacts: $('detail-facts'),
    detailHint: $('detail-hint'),
    detailFactors: $('detail-factors'),
    detailClose: $('detail-close'),
    weatherTitle: $('weather-title'),
    weatherWhen: $('weather-when'),
    weatherIcon: $('weather-icon'),
    weatherTemp: $('weather-temp'),
    weatherCondition: $('weather-condition'),
    weatherStats: $('weather-stats'),
    weatherImpact: $('weather-impact'),
  };

  const HOME = 'CLT';

  let airports = [];
  let airportsByCode = new Map();
  let flights = [];
  let flightsById = new Map();

  // load: 'loading' | 'ok' | 'quota_exhausted' | 'error'
  const state = { origin: HOME, dest: '', query: '', page: 0, load: 'loading', layoutKey: '', cols: [], rows: [], selectedRow: null };

  /* Formatting: flight times are shown in the local time of the airport they belong to */

  function boardTime(date, tz) {
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: tz }).replace(' ', '');
  }

  function readableTime(date, tz) {
    return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: tz });
  }

  // Gate, aircraft and airline names come from the flight-data provider, so escape them before using innerHTML.
  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  }

  function duration(min) {
    const h = Math.floor(min / 60);
    const m = min % 60;
    return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`;
  }

  function dayLabel(date, tz) {
    const day = (d) => d.toLocaleDateString('en-CA', { timeZone: tz });
    return day(date) === day(new Date())
      ? 'Today'
      : date.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric', timeZone: tz });
  }

  /* Filters */

  function fillSelect(select, anyLabel) {
    const options = airports.map((a) => `<option value="${a.code}">${a.city} (${a.code})</option>`).join('');
    select.innerHTML = (anyLabel ? `<option value="">${anyLabel}</option>` : '') + options;
  }

  function visibleFlights() {
    const start = Date.now() - WINDOW_BEFORE_MIN * 60e3;
    const end = Date.now() + WINDOW_AFTER_HOURS * 3600e3;
    const query = state.query.replace(/\s+/g, '').toUpperCase();
    return flights.filter(
      (f) =>
        f.departure >= start &&
        f.departure <= end &&
        (!state.dest || f.dest.code === state.dest) &&
        (!query || f.flightNumber.replace(' ', '').includes(query))
    );
  }

  /* Board layout */

  function columns() {
    return narrowQuery.matches
      ? [
          { key: 'time', label: 'Time', w: 7, align: 'right' },
          { key: 'flight', label: 'Flight', w: 7 },
          { key: 'to', label: 'To', w: 3 },
          { key: 'risk', label: 'Risk', w: 3, align: 'right', toned: true },
        ]
      : [
          { key: 'time', label: 'Time', w: 7, align: 'right' },
          { key: 'flight', label: 'Flight', w: 7 },
          { key: 'destination', label: 'Destination', w: 14 },
          { key: 'gate', label: 'Gate', w: 3 },
          { key: 'risk', label: 'Risk', w: 3, align: 'right', toned: true },
          { key: 'remarks', label: 'Remarks', w: 14, toned: true },
        ];
  }

  function rowsPerPage() {
    return narrowQuery.matches ? 10 : 12;
  }

  function cellText(f, key) {
    switch (key) {
      case 'time': return boardTime(f.departure, f.origin.tz);
      case 'flight': return f.flightNumber;
      case 'to': return f.dest.code;
      case 'destination': return f.dest.board;
      case 'gate': return f.gate || '-';
      case 'risk': return f.cancelled ? '-' : `${f.prediction.probability}%`;
      case 'remarks': return f.cancelled ? 'CANCELLED' : TIERS[f.prediction.tier].board;
      default: return '';
    }
  }

  function describe(f) {
    const tier = TIERS[f.prediction.tier];
    const gate = f.gate ? ` from gate ${f.gate}` : '';
    return `${f.flightNumber}, ${f.origin.city} to ${f.dest.city}, departs ${readableTime(f.departure, f.origin.tz)}${gate}. ${f.prediction.probability}% delay risk, ${tier.label.toLowerCase()}. Show details`;
  }

  function buildBoard() {
    const cols = columns();
    const count = rowsPerPage();
    const key = cols.map((c) => c.key).join() + count;
    if (key === state.layoutKey) return;
    state.layoutKey = key;
    state.cols = cols;

    els.head.replaceChildren(
      ...cols.map((c) => {
        const label = document.createElement('span');
        label.className = c.align === 'right' ? 'col col--right' : 'col';
        label.style.setProperty('--w', c.w);
        label.textContent = c.label;
        return label;
      })
    );

    els.rows.replaceChildren();
    state.rows = [];
    for (let i = 0; i < count; i++) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'row';
      btn.disabled = true;
      const cells = cols.map((col) => {
        const span = document.createElement('span');
        span.className = 'col';
        span.style.setProperty('--w', col.w);
        btn.appendChild(span);
        return { col, span, tiles: SplitFlap.create(span, col.w) };
      });
      btn.addEventListener('click', () => openDetail(btn.dataset.id, btn));
      els.rows.appendChild(btn);
      state.rows.push({ btn, cells });
    }
    sizeTiles();
  }

  // Fit the tile width so every column fills the board without scrolling.
  function sizeTiles() {
    const cols = state.cols;
    const tiles = cols.reduce((sum, c) => sum + c.w, 0);
    const rowPadding = narrowQuery.matches ? 12 : 20;
    const available = els.rows.clientWidth - rowPadding;
    const gap = 2;
    const width = (available - (tiles - cols.length) * gap) / (tiles + 0.7 * (cols.length - 1));
    els.board.style.setProperty('--tw', `${Math.max(8, Math.min(26, Math.floor(width * 2) / 2))}px`);
  }

  /* Rendering */

  function render() {
    buildBoard();
    const list = visibleFlights();
    const perPage = state.rows.length;
    const pages = Math.max(1, Math.ceil(list.length / perPage));
    state.page = Math.min(state.page, pages - 1);
    const slice = list.slice(state.page * perPage, (state.page + 1) * perPage);

    state.rows.forEach((row, i) => {
      const f = slice[i];
      row.btn.disabled = !f;
      if (f) {
        row.btn.dataset.id = f.id;
        row.btn.setAttribute('aria-label', describe(f));
      } else {
        delete row.btn.dataset.id;
        row.btn.removeAttribute('aria-label');
      }

      if (!list.length && i === 0) {
        writeAcrossColumns(row.cells, state.load === 'loading' ? ['LOADING'] : ['NO', 'FLIGHTS', 'FOUND']);
        return;
      }

      row.cells.forEach((cell, j) => {
        cell.span.dataset.tone = f && cell.col.toned ? (f.cancelled ? 'bad' : TIERS[f.prediction.tier].tone) : '';
        SplitFlap.write(cell.tiles, f ? cellText(f, cell.col.key) : '', {
          align: cell.col.align,
          delay: i * 45 + j * 30,
        });
      });
    });

    els.pageLabel.textContent = `Page ${state.page + 1} of ${pages}`;
    els.prev.disabled = state.page === 0;
    els.next.disabled = state.page >= pages - 1;
    els.status.textContent = statusText(list.length);
    els.clear.hidden = !(state.dest || state.query);
  }

  // Place whole words column by column so no word is split across a gap.
  function writeAcrossColumns(cells, words) {
    const queue = [...words];
    cells.forEach((cell, j) => {
      let text = '';
      while (queue.length && (text ? text.length + 1 : 0) + queue[0].length <= cell.col.w) {
        text += (text ? ' ' : '') + queue.shift();
      }
      cell.span.dataset.tone = '';
      SplitFlap.write(cell.tiles, text, { delay: j * 30 });
    });
  }

  function statusText(count) {
    const from = airportsByCode.get(state.origin);
    const to = airportsByCode.get(state.dest);
    const city = from ? from.city : state.origin;
    if (state.load === 'loading') return `Loading departures from ${city}…`;
    if (state.load === 'error') return `Couldn't load departures from ${city}. Try again in a minute.`;
    if (state.load === 'quota_exhausted' && !flights.length) {
      return `This month's free flight-data allowance is used up, so ${city} can't be loaded. Charlotte is always available.`;
    }
    const partial = state.load === 'quota_exhausted' ? ' Some days couldn\'t be loaded.' : '';
    if (!count) {
      return `No departures match these filters. Try a different airport or clear the flight number.${partial}`;
    }
    let text = `${count} ${count === 1 ? 'departure' : 'departures'}`;
    if (from) text += ` from ${from.city}`;
    if (to) text += ` to ${to.city}`;
    return `${text} in the next ${WINDOW_AFTER_HOURS} hours.${partial}`;
  }

  /* Flight detail */

  const riskTiles = SplitFlap.create(els.detailRisk, 3);

  function openDetail(id, rowBtn) {
    const f = flightsById.get(id);
    if (!f) return;
    const p = f.prediction;
    const tier = TIERS[p.tier];

    els.detailTitle.textContent = f.flightNumber;
    els.detailRoute.textContent = `${f.origin.city} (${f.origin.code}) to ${f.dest.city} (${f.dest.code})`;
    const tone = f.cancelled ? 'bad' : tier.tone;
    els.detailRisk.dataset.tone = tone;
    els.detailVerdict.dataset.tone = tone;
    els.detailVerdict.textContent = f.cancelled
      ? 'Cancelled by the airline'
      : p.expectedDelayMin ? `${tier.label}, by about ${p.expectedDelayMin} minutes` : tier.label;

    const tz = f.origin.tz;
    const estimated = new Date(f.departure.getTime() + p.expectedDelayMin * 60e3);
    const facts = [
      ['Scheduled departure', `${readableTime(f.departure, tz)}, ${dayLabel(f.departure, tz).replace('Today', 'today')}`],
      ['Estimated departure', f.cancelled ? '—' : readableTime(p.expectedDelayMin ? estimated : f.departure, tz)],
      ['Gate', f.gate || '—'],
      ['Flight time', f.durationMin ? duration(f.durationMin) : '—'],
      ['Airline', f.airline.name],
      ['Aircraft', f.aircraft || '—'],
    ];
    els.detailFacts.innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${escapeHtml(v)}</dd></div>`).join('');

    renderWeather(f);

    els.detailHint.textContent = `A typical flight starts at ${p.baseRisk}% risk. Each factor below moves that up or down by the points shown.`;
    els.detailFactors.innerHTML = p.factors
      .map((factor) => {
        const width = Math.min(Math.abs(factor.pts) / 24, 1) * 50;
        const direction = factor.pts >= 0 ? 'up' : 'down';
        const sign = factor.pts > 0 ? '+' : factor.pts < 0 ? '−' : '';
        return `<li class="factor">
          <span>
            <span class="factor__name">${factor.name}</span>
            <span class="factor__detail">${escapeHtml(factor.detail)}</span>
          </span>
          <span class="factor__bar" aria-hidden="true"><span class="factor__fill factor__fill--${direction}" style="width:${width}%"></span></span>
          <span class="factor__pts">${sign}${Math.abs(factor.pts)}</span>
        </li>`;
      })
      .join('');

    if (state.selectedRow) state.selectedRow.classList.remove('is-selected');
    state.selectedRow = rowBtn;
    rowBtn.classList.add('is-selected');

    SplitFlap.clear(riskTiles);
    els.dialog.showModal();
    SplitFlap.write(riskTiles, f.cancelled ? '-' : `${p.probability}%`, { align: 'right', delay: 160, stagger: 60 });
  }

  function renderWeather(f) {
    const wx = f.destWeather;
    els.weatherTitle.textContent = `Weather when you land in ${f.dest.city}`;
    if (!wx) {
      els.weatherWhen.textContent = 'The forecast for this arrival isn\'t available yet.';
      els.weatherIcon.setAttribute('href', '#wx-cloud');
      els.weatherTemp.textContent = '';
      els.weatherCondition.textContent = '';
      els.weatherStats.innerHTML = '';
      els.weatherImpact.dataset.tone = '';
      els.weatherImpact.textContent = 'Not affecting this flight\'s delay risk.';
      return;
    }
    const tz = f.dest.tz;
    const landing = f.arrival || f.departure;
    const day = dayLabel(landing, tz);
    els.weatherWhen.textContent = f.arrival
      ? `Forecast for ${readableTime(f.arrival, tz)} local time${day === 'Today' ? '' : `, ${day}`}`
      : 'Forecast for the expected arrival time';
    els.weatherIcon.setAttribute('href', `#wx-${wx.icon}`);
    els.weatherTemp.textContent = `${wx.tempF}°F`;
    els.weatherCondition.textContent = wx.condition;

    const stats = [
      ['Wind', wx.windDir ? `${wx.windMph} mph ${wx.windDir}` : 'Calm'],
      ['Visibility', `${wx.visibilityMi} ${wx.visibilityMi === 1 ? 'mile' : 'miles'}`],
      ['Chance of rain', `${wx.rainChance}%`],
    ];
    els.weatherStats.innerHTML = stats.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');

    const factor = f.prediction.factors.find((x) => x.name === 'Weather at arrival');
    const pts = factor ? factor.pts : 0;
    els.weatherImpact.dataset.tone = pts > 0 ? 'bad' : pts < 0 ? 'ok' : '';
    els.weatherImpact.textContent =
      pts > 0 ? `Adds ${pts} ${pts === 1 ? 'point' : 'points'} to this flight's delay risk.`
      : pts < 0 ? `Takes ${-pts} ${pts === -1 ? 'point' : 'points'} off this flight's delay risk.`
      : `Not affecting this flight's delay risk.`;
  }

  els.detailClose.addEventListener('click', () => els.dialog.close());
  els.dialog.addEventListener('click', (e) => {
    if (e.target === els.dialog) els.dialog.close();
  });
  els.dialog.addEventListener('close', () => {
    if (state.selectedRow) state.selectedRow.classList.remove('is-selected');
    state.selectedRow = null;
  });

  /* Clock */

  function tickClock() {
    const now = new Date();
    els.clockTime.textContent = readableTime(now);  // the viewer's own clock
    els.clockTime.dateTime = now.toISOString();
    els.clockDate.textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  }

  /* Wiring */

  // Departures for one airport at a time; a slow response for an airport the visitor has
  // already left is dropped.
  let loadSeq = 0;
  async function loadOrigin() {
    const seq = ++loadSeq;
    state.load = 'loading';
    flights = [];
    flightsById = new Map();
    render();
    try {
      const result = await loadDepartures(state.origin);
      if (seq !== loadSeq) return;
      flights = result.flights;
      flightsById = new Map(flights.map((f) => [f.id, f]));
      state.load = result.status;
    } catch (err) {
      if (seq !== loadSeq) return;
      console.error(err);
      state.load = 'error';
    }
    render();
  }

  function onFilterChange() {
    const originChanged = els.origin.value !== state.origin;
    state.origin = els.origin.value;
    state.dest = els.dest.value;
    state.query = els.flight.value.trim();
    state.page = 0;
    if (originChanged) loadOrigin();
    else render();
  }

  els.origin.addEventListener('change', onFilterChange);
  els.dest.addEventListener('change', onFilterChange);
  let searchTimer;
  els.flight.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(onFilterChange, 180);
  });

  els.clear.addEventListener('click', () => {
    els.dest.value = '';
    els.flight.value = '';
    onFilterChange();
    els.dest.focus();
  });

  els.prev.addEventListener('click', () => {
    state.page -= 1;
    render();
  });
  els.next.addEventListener('click', () => {
    state.page += 1;
    render();
  });

  narrowQuery.addEventListener('change', render);
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(sizeTiles, 100);
  });

  tickClock();
  setInterval(tickClock, 1000);
  setInterval(render, 60e3); // drop departed flights as time passes

  render();
  // Webfonts can change row width slightly; refit once they are ready.
  if (document.fonts) document.fonts.ready.then(sizeTiles);

  loadAirports()
    .then((list) => {
      airports = list;
      airportsByCode = new Map(airports.map((a) => [a.code, a]));
      fillSelect(els.origin);
      fillSelect(els.dest, 'Anywhere');
      els.origin.value = HOME;
      return loadOrigin();
    })
    .catch((err) => {
      console.error(err);
      state.load = 'error';
      render();
    });
})();
