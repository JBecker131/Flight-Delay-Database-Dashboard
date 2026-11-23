# Departures: the delay forecast site (Vercel)

The departures board from the prototype (`../index.html`), now showing the gradient boosting model's real predictions. Pick an airport, find your flight, and select it to see:

- **Risk**: a score out of 100, showing how the flight ranks against the model's 411,465 test flights (88 = riskier than 88% of them). It comes from the API's `risk_score`.
- **Delay**: the model's expected delay in minutes. The model can't size long delays well (test RMSE is about 54 minutes), so anything over 59 shows as **60+**. The exact figure is capped on the server and never reaches the browser.
- **Remarks**: the risk band. On time is a score under 50, Possible delay is 50 to 79, and Likely delayed is 80 or more.
- **In the flight's panel**: the actual chance of leaving 15+ and 60+ minutes late, with a short explanation. The expected delay is an average over similar flights, and a few very late flights pull it up, so a flight expected 25 minutes late still has only about a 1-in-3 chance of leaving 15+ minutes late.
- **What the model looked at**: that flight's inputs in plain words, such as the operating airline, weather at both airports, the airline's record and how busy the airport is.

The board shows the score rather than the 15+ minute chance, because the chance stays under 60% even for the riskiest flights and looked too low next to the minutes.

## How it fits together

```
browser ──> Vercel (this folder)
              public/            the board: plain HTML, CSS and JS, no build step
              api/airports       ──> live board on Modal  /api/airports
              api/departures     ──> live board on Modal  /api/departures   (real AeroDataBox schedules + forecast)
                                 ──> model on Modal       POST /predict     (batches of 500, Bearer key)
              api/explain        ──> model on Modal       POST /predict     (one flight, include_features)
```

The model's API key is only used inside `api/`, so it never reaches the browser. Departures are cached on Vercel's CDN for 5 minutes per airport, which keeps Modal and the AeroDataBox quota quiet. If the model is down, the board still loads and shows "NO FORECAST".

## Deploy

1. Import the GitHub repo in Vercel and set **Root Directory** to `web`. The framework preset is "Other", with no build command. `vercel.json` serves `public/`.
2. Add the environment variable **`PREDICT_API_KEY`** for Production and Preview. Use the same value as the `flight-delay-api` Modal secret. `MODEL_API_URL` and `LIVE_API_URL` are optional and default to the deployed Modal apps.
3. Redeploy the model on Modal first (`python -m modal deploy model/modal_app.py`), since `risk_score` is new. Until you do, the Risk column shows "-".
4. Deploy. You can also deploy from this folder with `npx vercel` and then `npx vercel --prod`.

Beyond that redeploy, nothing else on Modal changes. The site calls the model from the server, so its CORS setting (`ALLOWED_ORIGINS`) doesn't come into play.

## Run it locally

```sh
node dev-server.mjs                                    # needs PREDICT_API_KEY in the environment
MODEL_API_URL=http://localhost:8001 node dev-server.mjs  # or against a local model API with no key:
#   (from the repo root) python -m uvicorn model.api:app_from_env --factory --port 8001
npm test                                               # unit tests for the server code
```

Then open <http://localhost:3000>.

## Files

| File | What it does |
| --- | --- |
| `api/departures.js` | Live departures for an airport, each scored by the model |
| `api/explain.js` | One flight's model inputs, in plain words |
| `api/airports.js` | The airport list, cached for a day |
| `lib/predictions.js` | Builds model requests, batches them, maps risk to tiers, applies the 60+ cap |
| `lib/explain.js` | Turns the model's 53 features into readable lines |
| `lib/upstream.js` | Upstream URLs, the key, timeouts and JSON responses |
| `public/` | The board: `js/data.js` (API to flights), `js/app.js` (board and detail panel), `js/splitflap.js` |
