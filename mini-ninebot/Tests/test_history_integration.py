from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class HistoryIntegrationTests(unittest.TestCase):
    def test_history_api_uses_existing_authenticated_request_pipeline(self) -> None:
        client = source("Shared/NinebotServerClient.swift")
        method = client.split("func fetchVehicleHistory(", 1)[1].split("func login(", 1)[0]

        self.assertIn('method: "GET"', method)
        self.assertIn('path: ["vehicles", sn, "history"]', method)
        self.assertIn('URLQueryItem(name: "limit"', method)
        self.assertIn(
            'request.setValue("Bearer \\(token)", forHTTPHeaderField: "Authorization")',
            client,
        )

    def test_history_response_parser_covers_all_chart_fields_and_empty_list(self) -> None:
        client = source("Shared/NinebotServerClient.swift")
        parser = client.split("static func vehicleHistory(", 1)[1].split(
            "static func vehicleState(", 1
        )[0]

        for field in (
            "collected_at",
            "battery_percent",
            "battery_voltage",
            "battery_temperature",
            "estimated_range_km",
            "location",
            "source",
            "stale",
        ):
            self.assertIn(field, parser)

        self.assertIn('object["list"]?.arrayValue ?? []', parser)
        self.assertIn(".sorted { $0.collectedAt < $1.collectedAt }", parser)

    def test_empty_and_network_error_states_are_distinct(self) -> None:
        dashboard = source("mini-ninebot/App/NinebotDashboardView.swift")
        view_model = source("mini-ninebot/App/NinebotViewModel.swift")

        self.assertIn("正在积累历史数据", dashboard)
        self.assertIn("历史数据加载失败", dashboard)
        self.assertIn('Button("重试", action: onRetry)', dashboard)
        self.assertIn("serverHistoryErrors[sn] = error.localizedDescription", view_model)
        self.assertIn("serverHistoryErrors.removeValue(forKey: sn)", view_model)

    def test_chart_conversion_is_chronological_and_maps_four_metrics(self) -> None:
        dashboard = source("mini-ninebot/App/NinebotDashboardView.swift")
        chart = dashboard.split("private enum VehicleHistoryMetric", 1)[1].split(
            "private struct TripTrendRangeModelCard", 1
        )[0]

        self.assertIn(".sorted { $0.collectedAt < $1.collectedAt }", chart)
        self.assertIn("sample.date.timeIntervalSince1970", chart)
        for property_name in (
            "point.batteryPercent",
            "point.batteryVoltage",
            "point.batteryTemperature",
            "point.estimatedRangeKm",
        ):
            self.assertIn(property_name, chart)

    def test_server_history_has_priority_with_local_snapshot_fallback(self) -> None:
        dashboard = source("mini-ninebot/App/NinebotDashboardView.swift")
        trend_view = dashboard.split("private struct TripTrendView", 1)[1].split(
            "private struct TripTrendOfficialRangeCard", 1
        )[0]

        self.assertIn(
            "serverHistoryPoints.isEmpty ? localHistoryFallback : serverHistoryPoints",
            trend_view,
        )
        self.assertIn("batteryPercent: point.battery.map(Double.init)", trend_view)
        self.assertIn("estimatedRangeKm: point.endurance", trend_view)
        self.assertIn('source: "local"', trend_view)
        self.assertIn("服务器暂无历史，当前使用本地历史快照", dashboard)

    def test_empty_history_uses_official_range_without_algorithm_card(self) -> None:
        dashboard = source("mini-ninebot/App/NinebotDashboardView.swift")
        trend_view = dashboard.split("private struct TripTrendView", 1)[1].split(
            "private struct TripTrendOfficialRangeCard", 1
        )[0]

        self.assertIn("!serverHistoryPoints.isEmpty", trend_view)
        self.assertIn("TripTrendOfficialRangeCard(snapshot: snapshot)", trend_view)


if __name__ == "__main__":
    unittest.main()
