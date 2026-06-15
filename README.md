# Fuel Route Planner API

A Django REST API that plans the cheapest fuel stops along a driving route anywhere in the USA.

Given a start and end location, it returns an optimised list of truck stop fuel stations that minimises total fuel cost while keeping the vehicle moving. Vehicle is assumed to have a **500-mile range** and **10 MPG** fuel efficiency, starting with a full tank.

---

## How It Works

1. **Geocode** the start and end addresses via Nominatim (OpenStreetMap)
2. **Fetch the driving route** via OSRM (free) or OpenRouteService (if API key is set)
3. **Find fuel stations** within 25 miles of the route using bounding-box + haversine filtering against 6,600+ pre-loaded stations
4. **Optimise stops** with a greedy algorithm — at each step, pick the cheapest reachable station until the destination is reachable
5. All geocode and route results are **cached in Redis** to avoid repeat API calls

---

## Stack

| Layer | Technology |
|---|---|
| Framework | Django 5.2 + Django REST Framework |
| Database | PostgreSQL 18 |
| Cache | Redis 7 + django-redis |
| HTTP client | niquests (HTTP/2 drop-in for requests) |
| Geocoding | Nominatim (OpenStreetMap) — free, no key |
| Routing | OSRM public server — free, no key |
| Routing (optional) | OpenRouteService — set `ORS_API_KEY` in `.env` |
| Authentication | JWT via djangorestframework-simplejwt |
| Static files | WhiteNoise (serves admin CSS/JS from Gunicorn) |
| API docs | drf-spectacular (Swagger UI + ReDoc) |
| Tests | pytest + pytest-django |
| Container | Docker + Docker Compose |

---

## Prerequisites

- Docker and Docker Compose installed
- The OPIS fuel price CSV placed at `data/fuel-prices-for-be-assessment.csv`

---

## Quickstart

```bash
# 1. Copy environment file
cp .env.example .env
# Edit .env — set a real NOMINATIM_EMAIL (required by Nominatim ToS)

# 2. Build image and start all services
make build
make up

# 3. Run migrations
make migrate

# 4. Load fuel station data (~20-40 min first run — geocodes ~1,400 unique cities)
make load-data

# 5. Create your first user
docker-compose exec api python manage.py createsuperuser

# 6. Open the app
open http://localhost:8000/login/
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Django secret key |
| `DEBUG` | No | `True` for development (default: `True`) |
| `ALLOWED_HOSTS` | Yes | Comma-separated list of allowed hosts |
| `DB_NAME` | Yes | PostgreSQL database name |
| `DB_USER` | Yes | PostgreSQL username |
| `DB_PASSWORD` | Yes | PostgreSQL password |
| `DB_HOST` | Yes | PostgreSQL host (`postgres` inside Docker) |
| `DB_PORT` | No | PostgreSQL port (default: `5432`) |
| `REDIS_URL` | Yes | Redis connection URL (`redis://redis:6379/0` inside Docker) |
| `NOMINATIM_EMAIL` | Yes | Your real email — sent in Nominatim's `User-Agent` header as required by their ToS. Without a valid email you get HTTP 403. |
| `ORS_API_KEY` | No | OpenRouteService API key. If set, ORS is used instead of the public OSRM server |

---

## Authentication

The API uses **JWT (JSON Web Token)** authentication via Bearer tokens.

### Web UI flow

1. Visit `http://localhost:8000/login/` → sign in with username + password
2. No account yet? Click **Sign up** to register
3. After login, token is stored in `localStorage` and you're redirected to the map
4. Tokens expire after **60 minutes** — the app redirects to login automatically on expiry
5. Click **Sign out** to clear the token and return to login

### API flow (Postman / curl)

**Register a new account:**
```bash
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username": "yourname", "password": "yourpassword", "password2": "yourpassword"}'
```

**Get a token:**
```bash
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "yourname", "password": "yourpassword"}'
```

**Refresh an expired access token:**
```bash
curl -X POST http://localhost:8000/api/auth/token/refresh/ \
  -H "Content-Type: application/json" \
  -d '{"refresh": "<refresh_token>"}'
```

**Call the route endpoint with the token:**
```bash
curl -X GET "http://localhost:8000/api/route/?start=Chicago%2C%20IL&end=Los%20Angeles%2C%20CA" \
  -H "Authorization: Bearer <access_token>"
```

### Token lifetimes

| Token | Lifetime |
|---|---|
| Access token | 60 minutes |
| Refresh token | 7 days |

---

## API Endpoints

### Public endpoints (no token required)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health/` | Service health — Redis status + station count |
| `POST` | `/api/auth/register/` | Create a new user account |
| `POST` | `/api/auth/token/` | Obtain access + refresh tokens |
| `POST` | `/api/auth/token/refresh/` | Refresh an expired access token |
| `POST` | `/api/auth/token/verify/` | Verify a token is valid |
| `GET` | `/login/` | Login page (web UI) |
| `GET` | `/signup/` | Signup page (web UI) |
| `GET` | `/api/map/` | Interactive map page (web UI) |
| `GET` | `/api/docs/` | Swagger UI |
| `GET` | `/api/redoc/` | ReDoc |

### Protected endpoints (Bearer token required)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/route/` | Plan an optimised fuel route |

---

### `GET /api/route/`

**Query parameters:**

| Parameter | Required | Example |
|---|---|---|
| `start` | Yes | `Chicago, IL` |
| `end` | Yes | `Los Angeles, CA` |

**Example response (abbreviated):**

```json
{
  "route": {
    "start_location": { "address": "Chicago, IL", "lat": 41.88, "lon": -87.63 },
    "end_location": { "address": "Los Angeles, CA", "lat": 34.05, "lon": -118.24 },
    "total_distance_miles": 2015.3,
    "estimated_duration_hours": 28.4
  },
  "fuel_stops": [
    {
      "stop_number": 1,
      "station_name": "LOVES TRAVEL STOP #123",
      "city": "Des Moines",
      "state": "IA",
      "price_per_gallon": 2.899,
      "gallons_purchased": 48.1,
      "stop_cost": 139.55,
      "distance_from_start_miles": 481.0
    }
  ],
  "summary": {
    "total_fuel_cost": 285.50,
    "total_gallons_purchased": 98.4,
    "number_of_stops": 2,
    "fuel_efficiency_mpg": 10,
    "vehicle_range_miles": 500
  },
  "map_data": { "type": "FeatureCollection", "features": [] }
}
```

**Error responses:**

| Status | Reason |
|---|---|
| `400` | Missing or unresolvable location |
| `401` | Missing or invalid Bearer token |
| `422` | No fuel stations found within range |
| `502` | OSRM or ORS routing API failed |

---

### `GET /api/health/`

```json
{
  "status": "ok",
  "redis": true,
  "stations_loaded": 6624
}
```

---

## Make Commands

```bash
make setup          # Build, start services, and load fuel data (full first-time setup)
make build          # Build Docker image
make up             # Start all services in background
make down           # Stop all services
make restart        # Restart the API container
make logs           # Tail API logs

make load-data      # Load + geocode fuel stations from CSV
make migrate        # Run Django migrations
make makemigrations # Generate new migration files

make test           # Run full test suite inside Docker (130 tests, ~1s)
make test-local     # Run tests locally (requires pip install -r requirements.txt)

make shell          # Open Django shell
make psql           # Open PostgreSQL CLI
make redis-cli      # Open Redis CLI
```

---

## Running Tests

Tests run entirely in-memory (SQLite + LocMemCache) — no PostgreSQL or Redis needed:

```bash
make test
```

```
collected 130 items
...
130 passed in 0.94s
```

---

## Project Structure

```
backend_app/
├── config/
│   ├── settings.py          # Django settings, JWT config, Redis, env vars
│   └── urls.py              # Root URL routing + auth + OpenAPI endpoints
├── route_planner/
│   ├── models.py            # FuelStation model (single DB table)
│   ├── views.py             # HealthView, RouteView, MapView, LoginPageView,
│   │                        # SignupPageView, RegisterView
│   ├── apps.py              # Signal registration (cache invalidation on station change)
│   ├── constants.py         # Vehicle specs, API URLs, cache TTLs, USA bounding box
│   ├── services/
│   │   ├── geocoding.py     # Nominatim geocoding with USA validation
│   │   ├── routing.py       # OSRM / ORS route fetching
│   │   ├── fuel_optimizer.py # Haversine station filtering + greedy stop selection
│   │   └── cache_manager.py  # Redis cache-aside with distributed lock (flood prevention)
│   └── management/
│       └── commands/
│           └── load_fuel_data.py  # CSV import + Nominatim batch geocoding
├── templates/
│   ├── login.html           # Login page
│   ├── signup.html          # Signup page
│   └── map.html             # Leaflet.js interactive map
├── tests/                   # 130 pytest tests (no external calls)
├── data/                    # OPIS fuel price CSV
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── requirements.txt
```

---

## Caching Strategy

All external API calls are cached in Redis using a **cache-aside pattern with a distributed lock** to prevent thundering herd:

| Cache key | TTL | What is stored |
|---|---|---|
| `geocode:addr:<slug>` | 24 hours | Coordinates for a full address |
| `geocode:city:<state>:<city>` | 7 days | Coordinates for a city/state pair |
| `route:<slat>:<slon>:<elat>:<elon>` | 1 hour | Full OSRM route (distance, duration, geometry) |

When a `FuelStation` record is saved or deleted, all `route:*` cache entries are automatically invalidated via Django signals so stale prices never appear in results.

---

## Vehicle Assumptions

These are fixed constants (see `route_planner/constants.py`):

| Constant | Value |
|---|---|
| Fuel range | 500 miles |
| Fuel efficiency | 10 MPG |
| Max station deviation | 25 miles off-route |
| Tank at start | Full |
