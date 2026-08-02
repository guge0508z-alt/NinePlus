from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class ConnectionCheckTests(unittest.TestCase):
    def test_probe_checks_health_before_authenticated_vehicles(self) -> None:
        view_model = source("mini-ninebot/App/NinebotViewModel.swift")
        probe = view_model.split("func testConnection() async", 1)[1].split(
            "func refreshDashboard() async", 1
        )[0]

        self.assertLess(probe.index("client.healthCheck()"), probe.index("client.vehicleServiceCheck()"))

    def test_vehicle_probe_uses_the_existing_authenticated_request_path(self) -> None:
        client = source("Shared/NinebotServerClient.swift")
        self.assertIn("func vehicleServiceCheck() async throws", client)
        self.assertIn('request(method: "GET", path: ["vehicles"])', client)
        self.assertIn(
            'request.setValue("Bearer \\(token)", forHTTPHeaderField: "Authorization")',
            client,
        )

    def test_probe_distinguishes_all_required_outcomes(self) -> None:
        view_model = source("mini-ninebot/App/NinebotViewModel.swift")
        for state in (
            ".serverOffline",
            ".apiKeyInvalid",
            ".ninebotUnavailable",
            ".allNormal",
        ):
            self.assertIn(state, view_model)

        self.assertIn("statusCode == 401 || statusCode == 403", view_model)

    def test_both_connection_settings_surfaces_show_three_probe_rows(self) -> None:
        settings = source("mini-ninebot/App/NinebotSettingsView.swift")
        self.assertEqual(
            settings.count("ConnectionCheckStatusView(report: model.connectionCheck)"),
            2,
        )
        for title in ("服务器状态", "API Key 状态", "九号服务状态"):
            self.assertIn(title, settings)


if __name__ == "__main__":
    unittest.main()
