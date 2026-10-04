import unittest

from quantum_route_optimisation import optimize_classically
from route_dashboard.currency import (
    AUTO_DETECT,
    COUNTRY_CURRENCIES,
    ExchangeRates,
    MANUAL_SELECTION,
    build_currency_display,
    format_currency,
    resolve_country,
)
from route_dashboard.map_view import build_route_map
from route_dashboard.optimization import OptimizationRun, RouteSummary
from route_dashboard.scenario import build_demo_scenario


class CurrencyDisplayTests(unittest.TestCase):
    def test_country_to_currency_mapping_includes_examples_and_broader_countries(self) -> None:
        expected = {
            "India": "INR",
            "USA": "USD",
            "UK": "GBP",
            "Germany": "EUR",
            "France": "EUR",
            "Japan": "JPY",
            "Australia": "AUD",
            "Canada": "CAD",
            "Singapore": "SGD",
            "UAE": "AED",
            "Brazil": "BRL",
            "South Africa": "ZAR",
        }
        for country, currency in expected.items():
            with self.subTest(country=country):
                self.assertEqual(COUNTRY_CURRENCIES[country], currency)

    def test_currency_formatting_uses_symbols_grouping_and_currency_precision(self) -> None:
        self.assertEqual(format_currency(4250, "INR"), "₹4,250.00")
        self.assertEqual(format_currency(50.2, "USD"), "$50.20")
        self.assertEqual(format_currency(39.1, "GBP"), "£39.10")
        self.assertEqual(format_currency(45.8, "EUR"), "€45.80")
        self.assertEqual(format_currency(7200, "JPY"), "¥7,200")
        self.assertEqual(format_currency(1234.5, "AUD"), "A$1,234.50")

    def test_manual_country_selection_overrides_browser_locale(self) -> None:
        resolution = resolve_country(
            MANUAL_SELECTION,
            "en-US,en;q=0.9",
            manual_country="Japan",
        )
        self.assertEqual(resolution.country, "Japan")
        self.assertEqual(resolution.method, "Manual selection")

    def test_auto_detection_uses_browser_region_or_falls_back_to_india(self) -> None:
        detected = resolve_country(AUTO_DETECT, "en-US,en;q=0.9")
        fallback = resolve_country(AUTO_DETECT, None)

        self.assertEqual(detected.country, "USA")
        self.assertEqual(detected.method, "Browser language/region")
        self.assertEqual(fallback.country, "India")
        self.assertEqual(fallback.method, "Fallback default")

    def test_missing_exchange_rate_displays_usd_without_relabeling(self) -> None:
        display = build_currency_display("India", None)

        self.assertEqual(display.display_currency_code, "USD")
        self.assertEqual(display.format_money(50.2), "$50.20")
        self.assertIn("Showing amounts in USD", display.fallback_message)

    def test_exchange_rate_conversion_is_presentation_only(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        original_cost = result.total_cost
        display = build_currency_display(
            "India",
            ExchangeRates("USD", {"USD": 1.0, "INR": 84.0}, "test timestamp"),
        )

        self.assertEqual(display.format_money(original_cost), format_currency(original_cost * 84, "INR"))
        self.assertEqual(result.total_cost, original_cost)
        self.assertEqual(scenario.cost_weights.distance_cost_per_km, 0.099)

    def test_route_map_popup_uses_selected_currency(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        summary = RouteSummary(
            routes=result.routes,
            total_cost=result.total_cost,
            total_distance_km=sum(route.distance_km for route in result.routes),
            total_travel_time_min=sum(route.travel_time_min for route in result.routes),
        )
        run = OptimizationRun(
            classical=summary,
            quantum=None,
            quantum_result=None,
            quantum_error="not requested in test",
            objective_delta=None,
            relative_gap_percent=None,
        )
        display = build_currency_display(
            "Japan",
            ExchangeRates("USD", {"USD": 1.0, "JPY": 150.0}, "test timestamp"),
        )

        map_view = build_route_map(
            scenario,
            run,
            show_quantum=False,
            currency_display=display,
        )

        map_html = map_view.map.get_root().render()
        self.assertTrue(
            any(display.format_money(route.total_cost) in map_html for route in result.routes)
        )


if __name__ == "__main__":
    unittest.main()