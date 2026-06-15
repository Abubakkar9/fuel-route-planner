"""
Management command: python manage.py load_fuel_data

Reads the OPIS fuel prices CSV, deduplicates by station (keeping the lowest
retail price per OPIS ID), filters out Canadian stations, bulk-upserts into
the FuelStation table, then geocodes any stations missing coordinates.

Geocoding hits Nominatim at city-level (many stations share a city) with a
1.1 s delay between requests (Nominatim ToS). Coordinate updates are flushed
to the DB in batches — no N+1 saves. Re-runs skip already-geocoded stations.
"""
import csv
import time
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from route_planner.models import FuelStation
from route_planner.services.geocoding import geocode_city_state_batch

logger = logging.getLogger(__name__)

CANADIAN_PROVINCES = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT", "NT", "PE", "NL"}
GEOCODE_DELAY = 1.1   # seconds between Nominatim requests (rate limit: 1 req/s)
BATCH_SIZE = 100      # rows per bulk_update flush during geocoding


class Command(BaseCommand):
    help = "Load fuel station prices from CSV and geocode station locations"

    def handle(self, *args, **options):
        self._load_csv()
        self._geocode_stations()

    # ── Step 1: parse CSV and upsert ─────────────────────────────────────────

    def _load_csv(self):
        self.stdout.write("Loading fuel station data from CSV...")
        csv_path = settings.FUEL_CSV_PATH

        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f"CSV not found: {csv_path}"))
            return

        best_by_id: dict = {}

        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                state = row.get("State", "").strip().upper()
                if state in CANADIAN_PROVINCES:
                    continue
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price = float(row["Retail Price"])
                except (KeyError, ValueError):
                    continue

                if opis_id not in best_by_id or price < best_by_id[opis_id]["retail_price"]:
                    best_by_id[opis_id] = {
                        "opis_id": opis_id,
                        "name": row.get("Truckstop Name", "").strip(),
                        "address": row.get("Address", "").strip(),
                        "city": row.get("City", "").strip(),
                        "state": state,
                        "retail_price": price,
                    }

        self.stdout.write(f"  Parsed {len(best_by_id)} unique US stations")

        existing = {
            s.opis_id: s
            for s in FuelStation.objects.only(
                "id", "opis_id", "name", "address", "city", "state", "retail_price"
            )
        }
        to_create, to_update = [], []

        for data in best_by_id.values():
            if data["opis_id"] in existing:
                s = existing[data["opis_id"]]
                s.name = data["name"]
                s.address = data["address"]
                s.city = data["city"]
                s.state = data["state"]
                s.retail_price = data["retail_price"]
                to_update.append(s)
            else:
                to_create.append(FuelStation(**data))

        if to_create:
            FuelStation.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            FuelStation.objects.bulk_update(
                to_update,
                ["name", "address", "city", "state", "retail_price"],
                batch_size=500,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"  Created {len(to_create)} | Updated {len(to_update)} stations"
            )
        )

    # ── Step 2: geocode stations missing coordinates ──────────────────────────

    def _geocode_stations(self):
        total = FuelStation.objects.filter(geocoded=False).count()
        if total == 0:
            self.stdout.write("  All stations already geocoded")
            return

        unique_cities = (
            FuelStation.objects.filter(geocoded=False)
            .values("city", "state")
            .distinct()
            .count()
        )
        eta_min = unique_cities * GEOCODE_DELAY / 60
        self.stdout.write(
            f"  Geocoding {total} stations ({unique_cities} unique cities, ≈{eta_min:.0f} min)..."
        )

        city_cache: dict = {}
        pending: list = []
        geocoded_count = 0

        for station in FuelStation.objects.filter(geocoded=False).iterator():
            city_key = (station.city, station.state)

            if city_key not in city_cache:
                t0 = time.monotonic()
                lat, lon = geocode_city_state_batch(station.city, station.state)
                city_cache[city_key] = (lat, lon)
                elapsed = time.monotonic() - t0
                wait = GEOCODE_DELAY - elapsed
                if wait > 0:
                    time.sleep(wait)

            lat, lon = city_cache[city_key]
            if lat is not None:
                station.lat = lat
                station.lon = lon
                station.geocoded = True
                pending.append(station)
                geocoded_count += 1

            if len(pending) >= BATCH_SIZE:
                FuelStation.objects.bulk_update(pending, ["lat", "lon", "geocoded"], batch_size=BATCH_SIZE)
                pending.clear()

        if pending:
            FuelStation.objects.bulk_update(pending, ["lat", "lon", "geocoded"], batch_size=BATCH_SIZE)

        self.stdout.write(
            self.style.SUCCESS(f"  Geocoded {geocoded_count}/{total} stations")
        )
