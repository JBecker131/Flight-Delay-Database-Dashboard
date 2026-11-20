"""The delay model as a web API, for the Vercel site.

    GET  /health    liveness, no auth
    GET  /model     what the model is, its test scores, and how fresh its lookups are
    POST /predict   score up to 500 flights at once
    GET  /predict   score one flight from query parameters (handy for testing in a browser)

A flight is its airline, flight number (optional, used to find the regional that operates it),
origin, destination, and scheduled departure as local time at the origin, e.g.
    {"airline": "AA", "flight_number": "5069", "origin": "CLT", "dest": "DFW",
     "departure": "2026-11-21T09:05", "arrival": "2026-11-21T11:20"}
The arrival (local time at the destination) is optional. An `id` is echoed back to match results up.

Settings, from the environment (a Modal secret when deployed):
  PREDICT_API_KEY      if set, /predict needs "Authorization: Bearer <key>" (or "X-API-Key: <key>").
                       Call the API from Vercel's server side (a route handler) so the key stays secret.
  ALLOWED_ORIGINS      comma-separated origins browsers may call from, e.g. https://my-app.vercel.app.
                       Default "*". Only matters for calls made straight from the browser.
  ALLOWED_ORIGIN_REGEX optional, e.g. https://my-app-.*\\.vercel\\.app for preview deployments

Run it locally against Supabase:
    python -m uvicorn model.api:app_from_env --factory --port 8001
"""
import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from model import features as feat
from model.service import Predictor

MAX_FLIGHTS = 500


class FlightIn(BaseModel):
    id: str | int | None = Field(None, description="Echoed back, to match results to your rows")
    airline: str = Field(..., min_length=2, max_length=3, examples=["AA"])
    flight_number: str | int | None = Field(None, examples=["5069"])
    origin: str = Field(..., min_length=3, max_length=3, examples=["CLT"])
    dest: str = Field(..., min_length=3, max_length=3, examples=["DFW"])
    departure: str = Field(..., description="Scheduled departure, local time at the origin",
                           examples=["2026-11-21T09:05"])
    arrival: str | None = Field(None, description="Scheduled arrival, local time at the destination",
                                examples=["2026-11-21T11:20"])

    def flight(self):
        return feat.Flight(**self.model_dump(exclude={"id"}))


class PredictRequest(BaseModel):
    flights: list[FlightIn] = Field(..., min_length=1, max_length=MAX_FLIGHTS)
    include_features: bool = Field(False, description="Also return the 53 model inputs for each flight")


def create_app(predictor: Predictor, api_key=None, allowed_origins=("*",), origin_regex=None):
    app = FastAPI(title="Flight delay model", version="1.0",
                  description="Expected departure delay and risk for US domestic flights.")
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins), allow_origin_regex=origin_regex,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-API-Key"],
                       max_age=3600)

    def authorized(authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
        if not api_key:
            return
        given = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(given.encode(), api_key.encode()):
            raise HTTPException(401, "Missing or wrong API key")

    def run(flights, include_features, response):
        response.headers["Cache-Control"] = "no-store"
        results = predictor.predict([f.flight() for f in flights], include_features=include_features)
        return {"predictions": [{"id": f.id, **r} for f, r in zip(flights, results)]}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/model")
    def model_info():
        return predictor.info()

    @app.post("/predict", dependencies=[Depends(authorized)])
    def predict(body: PredictRequest, response: Response):
        return run(body.flights, body.include_features, response)

    @app.get("/predict", dependencies=[Depends(authorized)])
    def predict_one(response: Response, airline: str, origin: str, dest: str, departure: str,
                    arrival: str | None = None, flight_number: str | None = None,
                    include_features: bool = Query(False)):
        try:
            flight = FlightIn(airline=airline, origin=origin, dest=dest, departure=departure, arrival=arrival,
                              flight_number=flight_number)
        except ValidationError as error:
            raise HTTPException(422, error.errors(include_url=False, include_context=False)) from None
        result = run([flight], include_features, response)["predictions"][0]
        if result["error"]:
            raise HTTPException(422, result["error"])
        return result

    return app


def predictor_from_supabase():
    from model import lookups
    from model.service import load_artifacts

    db = lookups.Database(lookups.database_url())
    model, feature_meta, serving, airports = load_artifacts()
    return Predictor(model, feature_meta, serving, airports, lookups.SupabaseLookups(db),
                     lookups.Weather(db, airports))


def app_from_env():
    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    return create_app(predictor_from_supabase(), api_key=os.environ.get("PREDICT_API_KEY") or None,
                      allowed_origins=origins or ["*"], origin_regex=os.environ.get("ALLOWED_ORIGIN_REGEX") or None)
