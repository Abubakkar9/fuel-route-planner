"""
Tests for route_planner/management/commands/load_fuel_data.py

Covers:
- CSV parsing: deduplication (lowest price per OPIS ID), Canadian province filtering
- DB upsert: creates new stations, updates prices on existing ones
- Geocoding step: uses city-level cache, skips already-geocoded stations,
  bulk-updates in batches
"""
import csv
import io
import pytest
from unittest.mock import patch, MagicMock, call
from django.core.management import call_command
from django.test import override_settings

from route_planner.models import FuelStation

CSV_HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


def make_csv(*rows):
    """
    Builds an in-memory CSV string from (opis_id, name, city, state, price) tuples.
    """
    lines = CSV_HEADER
    for opis_id, name, city, state, price in rows:
        lines += f"{opis_id},{name},123 Main St,{city},{state},100,{price}\n"
    return lines


@pytest.fixture
def tmp_csv(tmp_path):
    """Returns a factory that writes CSV content to a temp file and yields its path."""
    def _write(content):
        p = tmp_path / "fuel.csv"
        p.write_text(content)
        return p
    return _write


# ── CSV parsing ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCSVParsing:
    def test_creates_us_stations(self, tmp_csv):
        path = tmp_csv(make_csv((1, "Station A", "Chicago", "IL", "3.50")))
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(41.88, -87.63)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        assert FuelStation.objects.filter(opis_id=1).exists()

    def test_filters_out_canadian_stations(self, tmp_csv):
        content = make_csv(
            (1, "US Station", "Chicago", "IL", "3.50"),
            (2, "CA Station", "Toronto", "ON", "1.20"),  # Canadian province
            (3, "CA Station", "Calgary", "AB", "1.10"),
        )
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(41.88, -87.63)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        assert FuelStation.objects.filter(opis_id=1).exists()
        assert not FuelStation.objects.filter(opis_id=2).exists()
        assert not FuelStation.objects.filter(opis_id=3).exists()

    def test_keeps_lowest_price_per_opis_id(self, tmp_csv):
        # Same station ID appears twice — should keep the $3.00 row
        content = make_csv(
            (1, "Station A", "Chicago", "IL", "3.50"),
            (1, "Station A", "Chicago", "IL", "3.00"),
        )
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(41.88, -87.63)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        station = FuelStation.objects.get(opis_id=1)
        assert float(station.retail_price) == pytest.approx(3.00, abs=0.001)

    def test_skips_rows_with_invalid_price(self, tmp_csv):
        content = CSV_HEADER + "99,Bad Station,Addr,City,TX,100,NOT_A_NUMBER\n"
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(30.0, -97.0)):
                call_command("load_fuel_data")
        assert not FuelStation.objects.filter(opis_id=99).exists()

    def test_handles_missing_csv_gracefully(self, tmp_path):
        missing = tmp_path / "nonexistent.csv"
        with override_settings(FUEL_CSV_PATH=missing):
            # Should not raise — logs error and returns
            call_command("load_fuel_data")
        assert FuelStation.objects.count() == 0


# ── DB upsert ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDBUpsert:
    def test_creates_new_stations_on_first_run(self, tmp_csv):
        content = make_csv(
            (10, "Station X", "Dallas", "TX", "3.20"),
            (11, "Station Y", "Austin", "TX", "3.10"),
        )
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(30.0, -97.0)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        assert FuelStation.objects.filter(opis_id__in=[10, 11]).count() == 2

    def test_updates_price_on_existing_station(self, tmp_csv, station_factory):
        station_factory(opis_id=20, price=3.50, city="Houston", state="TX",
                        lat=29.76, lon=-95.37, geocoded=True)
        # Re-run with a lower price
        content = make_csv((20, "Updated Station", "Houston", "TX", "3.00"))
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(29.76, -95.37)):
                call_command("load_fuel_data")
        station = FuelStation.objects.get(opis_id=20)
        assert float(station.retail_price) == pytest.approx(3.00, abs=0.001)

    def test_does_not_duplicate_stations_on_rerun(self, tmp_csv):
        content = make_csv((30, "Station Z", "Phoenix", "AZ", "3.40"))
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(33.45, -112.07)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
                    call_command("load_fuel_data")
        assert FuelStation.objects.filter(opis_id=30).count() == 1


# ── Geocoding step ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGeocodingStep:
    def test_geocodes_stations_and_saves_coords(self, tmp_csv):
        content = make_csv((40, "Station A", "Denver", "CO", "3.60"))
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(39.74, -104.99)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        s = FuelStation.objects.get(opis_id=40)
        assert s.geocoded is True
        assert s.lat == pytest.approx(39.74, abs=0.01)
        assert s.lon == pytest.approx(-104.99, abs=0.01)

    def test_stations_sharing_city_make_one_geocode_call(self, tmp_csv):
        # Two stations in same city — only 1 Nominatim call expected
        content = make_csv(
            (50, "Station A", "Denver", "CO", "3.60"),
            (51, "Station B", "Denver", "CO", "3.70"),
        )
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(39.74, -104.99)) as mock_geo:
                with patch("time.sleep"):  # skip rate-limit delay in tests
                    call_command("load_fuel_data")
        mock_geo.assert_called_once_with("Denver", "CO")

    def test_skips_already_geocoded_stations(self, tmp_csv, station_factory):
        # Pre-existing geocoded station should not be re-geocoded
        station_factory(opis_id=60, city="Seattle", state="WA",
                        lat=47.61, lon=-122.33, geocoded=True)
        content = make_csv((60, "Station C", "Seattle", "WA", "3.80"))
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(47.61, -122.33)) as mock_geo:
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        mock_geo.assert_not_called()

    def test_station_with_failed_geocode_remains_ungeocodeed(self, tmp_csv):
        content = make_csv((70, "Remote Station", "Nowhere", "WY", "4.00"))
        path = tmp_csv(content)
        with override_settings(FUEL_CSV_PATH=path):
            with patch("route_planner.management.commands.load_fuel_data.geocode_city_state",
                       return_value=(None, None)):
                with patch("time.sleep"):
                    call_command("load_fuel_data")
        s = FuelStation.objects.get(opis_id=70)
        assert s.geocoded is False
        assert s.lat is None
