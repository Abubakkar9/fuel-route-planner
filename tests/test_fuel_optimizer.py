"""
Tests for route_planner/services/fuel_optimizer.py

Covers:
- haversine distance formula
- route sampling
- cumulative distance calculation
- find_stations_on_route (bounding box + deviation filter)
- optimize_fuel_stops (greedy algorithm, edge cases, short-trip cost)
"""
import pytest
from route_planner.services.fuel_optimizer import (
    haversine,
    _sample_route,
    _cumulative_distances,
    find_stations_on_route,
    optimize_fuel_stops,
)
from route_planner.models import FuelStation
from tests.conftest import make_station_dict


# ── haversine ────────────────────────────────────────────────────────────────

class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine(41.88, -87.63, 41.88, -87.63) == 0.0

    def test_chicago_to_nyc_approx_713_miles(self):
        # Chicago (41.88, -87.63) → NYC (40.71, -74.01) ≈ 713 miles
        dist = haversine(41.88, -87.63, 40.71, -74.01)
        assert 690 < dist < 730

    def test_chicago_to_la_approx_1745_miles(self):
        dist = haversine(41.88, -87.63, 34.05, -118.24)
        assert 1720 < dist < 1770

    def test_returns_positive_value(self):
        assert haversine(30.0, -90.0, 40.0, -100.0) > 0

    def test_symmetric(self):
        d1 = haversine(41.88, -87.63, 40.71, -74.01)
        d2 = haversine(40.71, -74.01, 41.88, -87.63)
        assert abs(d1 - d2) < 0.001

    def test_float_guard_no_domain_error(self):
        # Identical points can produce a value just above 0 due to floating-point;
        # the min(a, 1.0) guard prevents math.asin domain errors.
        result = haversine(0.0, 0.0, 0.0, 0.0000001)
        assert result >= 0


# ── _sample_route ────────────────────────────────────────────────────────────

class TestSampleRoute:
    def test_short_route_unchanged(self):
        coords = [(i, i) for i in range(10)]
        assert _sample_route(coords) == coords

    def test_long_route_sampled_to_target(self):
        coords = [(float(i), float(i)) for i in range(2000)]
        sampled = _sample_route(coords)
        assert len(sampled) <= 510  # 500 target + a few rounding

    def test_last_point_always_preserved(self):
        coords = [(float(i), float(i)) for i in range(2000)]
        sampled = _sample_route(coords)
        assert sampled[-1] == coords[-1]


# ── _cumulative_distances ────────────────────────────────────────────────────

class TestCumulativeDistances:
    def test_single_point_returns_zero(self):
        assert _cumulative_distances([(41.0, -87.0)]) == [0.0]

    def test_always_starts_at_zero(self):
        coords = [(41.0, -87.0), (42.0, -88.0)]
        dists = _cumulative_distances(coords)
        assert dists[0] == 0.0

    def test_monotonically_increasing(self):
        coords = [(41.0 + i * 0.5, -87.0) for i in range(5)]
        dists = _cumulative_distances(coords)
        assert all(dists[i] < dists[i + 1] for i in range(len(dists) - 1))

    def test_length_matches_coords(self):
        coords = [(float(i), float(-i)) for i in range(5)]
        assert len(_cumulative_distances(coords)) == 5


# ── find_stations_on_route ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestFindStationsOnRoute:
    # Route: straight east along ~41°N latitude from lon=-100 to lon=-87
    ROUTE_COORDS = [(41.0, -100.0 + i * 0.5) for i in range(27)]

    def test_station_on_route_is_included(self, station_factory):
        # Place station exactly on the route midpoint
        s = station_factory(opis_id=1, lat=41.0, lon=-93.5, price=3.50)
        qs = FuelStation.objects.filter(geocoded=True)
        result = find_stations_on_route(self.ROUTE_COORDS, qs)
        assert any(r["opis_id"] == s.opis_id for r in result)

    def test_station_far_from_route_is_excluded(self, station_factory):
        # Place station 200 miles north of route — well beyond 25-mile deviation
        station_factory(opis_id=2, lat=44.0, lon=-93.5, price=3.00)
        qs = FuelStation.objects.filter(geocoded=True)
        result = find_stations_on_route(self.ROUTE_COORDS, qs)
        assert not any(r["opis_id"] == 2 for r in result)

    def test_ungeocodeed_station_excluded(self, station_factory):
        station_factory(opis_id=3, lat=41.0, lon=-93.5, geocoded=False)
        qs = FuelStation.objects.filter(geocoded=True)
        result = find_stations_on_route(self.ROUTE_COORDS, qs)
        assert not any(r["opis_id"] == 3 for r in result)

    def test_results_sorted_by_distance_from_start(self, station_factory):
        station_factory(opis_id=10, lat=41.0, lon=-88.0, price=3.50)
        station_factory(opis_id=11, lat=41.0, lon=-95.0, price=3.50)
        station_factory(opis_id=12, lat=41.0, lon=-91.0, price=3.50)
        qs = FuelStation.objects.filter(geocoded=True)
        result = find_stations_on_route(self.ROUTE_COORDS, qs)
        dists = [r["distance_from_start"] for r in result]
        assert dists == sorted(dists)

    def test_result_contains_expected_fields(self, station_factory):
        station_factory(opis_id=20, lat=41.0, lon=-93.5, price=3.75)
        qs = FuelStation.objects.filter(geocoded=True)
        result = find_stations_on_route(self.ROUTE_COORDS, qs)
        assert result
        keys = {"opis_id", "name", "address", "city", "state", "price", "lat", "lon",
                "distance_from_start", "deviation_miles"}
        assert keys.issubset(result[0].keys())

    def test_empty_queryset_returns_empty_list(self):
        qs = FuelStation.objects.none()
        result = find_stations_on_route(self.ROUTE_COORDS, qs)
        assert result == []


# ── optimize_fuel_stops ──────────────────────────────────────────────────────

class TestOptimizeFuelStops:

    # ── Short trips (under 500 miles) ───────────────────────────────────────

    def test_short_trip_returns_no_stops(self):
        stations = [make_station_dict(price=3.00, distance_from_start=100.0)]
        stops, _ = optimize_fuel_stops(stations, total_distance_miles=400.0)
        assert stops == []

    def test_short_trip_estimates_cost_using_cheapest_station(self):
        # 400 miles / 10 mpg = 40 gallons × $3.00 = $120.00
        stations = [make_station_dict(price=3.00, distance_from_start=100.0)]
        _, cost = optimize_fuel_stops(stations, total_distance_miles=400.0)
        assert cost == pytest.approx(120.00, abs=0.01)

    def test_short_trip_uses_cheapest_of_multiple_stations(self):
        stations = [
            make_station_dict(opis_id=1, price=4.00, distance_from_start=50.0),
            make_station_dict(opis_id=2, price=2.50, distance_from_start=150.0),
        ]
        _, cost = optimize_fuel_stops(stations, total_distance_miles=400.0)
        # Should use $2.50: 40 gal × $2.50 = $100.00
        assert cost == pytest.approx(100.00, abs=0.01)

    def test_short_trip_with_no_nearby_stations_returns_zero_cost(self):
        _, cost = optimize_fuel_stops([], total_distance_miles=400.0)
        assert cost == 0.0

    def test_exactly_500_miles_needs_no_stop(self):
        stations = [make_station_dict(price=3.00, distance_from_start=250.0)]
        stops, _ = optimize_fuel_stops(stations, total_distance_miles=500.0)
        assert stops == []

    # ── Trips requiring stops ────────────────────────────────────────────────

    def test_501_mile_trip_requires_one_stop(self):
        # Station at mile 300 — only reachable option within 500-mile range
        stations = [make_station_dict(price=3.50, distance_from_start=300.0)]
        stops, _ = optimize_fuel_stops(stations, total_distance_miles=501.0)
        assert len(stops) == 1

    def test_greedy_picks_cheapest_reachable_station(self):
        # Two stations both reachable in first leg; should pick cheaper one
        stations = [
            make_station_dict(opis_id=1, price=4.00, distance_from_start=200.0),
            make_station_dict(opis_id=2, price=2.99, distance_from_start=300.0),
        ]
        stops, _ = optimize_fuel_stops(stations, total_distance_miles=600.0)
        assert stops[0]["opis_id"] == 2

    def test_stop_cost_calculation_is_correct(self):
        # 600-mile trip, stop at mile 300, $3.00/gal
        # Arrive at stop with 500-300=200 miles remaining
        # Fill: 300 miles / 10 mpg = 30 gallons × $3.00 = $90.00
        stations = [make_station_dict(price=3.00, distance_from_start=300.0)]
        stops, total = optimize_fuel_stops(stations, total_distance_miles=600.0)
        assert stops[0]["gallons_purchased"] == pytest.approx(30.0, abs=0.1)
        assert stops[0]["stop_cost"] == pytest.approx(90.0, abs=0.1)
        assert total == pytest.approx(90.0, abs=0.1)

    def test_multiple_stops_accumulate_total_cost(self):
        # 1200-mile trip, stops at 400 and 900 miles
        stations = [
            make_station_dict(opis_id=1, price=3.00, distance_from_start=400.0),
            make_station_dict(opis_id=2, price=3.50, distance_from_start=900.0),
        ]
        stops, total = optimize_fuel_stops(stations, total_distance_miles=1200.0)
        assert len(stops) == 2
        assert total == pytest.approx(stops[0]["stop_cost"] + stops[1]["stop_cost"], abs=0.01)

    def test_stop_numbers_are_sequential(self):
        stations = [
            make_station_dict(opis_id=1, price=3.00, distance_from_start=400.0),
            make_station_dict(opis_id=2, price=3.00, distance_from_start=800.0),
        ]
        stops, _ = optimize_fuel_stops(stations, total_distance_miles=1000.0)
        assert [s["stop_number"] for s in stops] == [1, 2]

    def test_no_reachable_station_raises_value_error(self):
        # Trip needs a stop but no station is within range
        with pytest.raises(ValueError, match="No fuel stations found"):
            optimize_fuel_stops([], total_distance_miles=1000.0)

    def test_unreachable_gap_raises_value_error(self):
        # Station at mile 600 is unreachable from start (> 500-mile range)
        stations = [make_station_dict(price=3.00, distance_from_start=600.0)]
        with pytest.raises(ValueError, match="No fuel stations found"):
            optimize_fuel_stops(stations, total_distance_miles=800.0)
