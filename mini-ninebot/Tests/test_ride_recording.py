from pathlib import Path
import plistlib
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class RideRecordingTests(unittest.TestCase):
    def test_main_app_declares_background_location_mode(self) -> None:
        with (PROJECT_ROOT / "Config/mini-ninebot-Info.plist").open("rb") as stream:
            info = plistlib.load(stream)

        self.assertIn("location", info["UIBackgroundModes"])
        self.assertIn("fetch", info["UIBackgroundModes"])
        self.assertIn("NSLocationWhenInUseUsageDescription", info)

    def test_recording_enables_background_location_and_idle_timer_only_while_active(self) -> None:
        recorder = source("mini-ninebot/App/NinebotRecordingView.swift")
        system_state = recorder.split(
            "private func configureRecordingSystemState(isActive: Bool)", 1
        )[1].split("nonisolated func locationManagerDidChangeAuthorization", 1)[0]

        self.assertIn("manager.allowsBackgroundLocationUpdates = isActive", system_state)
        self.assertIn("manager.showsBackgroundLocationIndicator = isActive", system_state)
        self.assertIn("UIApplication.shared.isIdleTimerDisabled = isActive", system_state)
        self.assertIn("configureRecordingSystemState(isActive: true)", recorder)
        self.assertGreaterEqual(
            recorder.count("configureRecordingSystemState(isActive: false)"),
            2,
        )

    def test_recorder_is_owned_by_the_app_root_across_tab_and_background_transitions(self) -> None:
        content = source("mini-ninebot/ContentView.swift")
        recording_view = source("mini-ninebot/App/NinebotRecordingView.swift")

        self.assertIn("@StateObject private var rideRecorder = NinebotRideRecorder()", content)
        self.assertIn("NinebotRecordingView(model: model, recorder: rideRecorder)", content)
        self.assertIn("@ObservedObject var recorder: NinebotRideRecorder", recording_view)

    def test_recording_stop_releases_continuous_sensors(self) -> None:
        recorder = source("mini-ninebot/App/NinebotRecordingView.swift")
        stop = recorder.split("func stop() -> NinebotRecordedRide?", 1)[1].split(
            "private func configureRecordingSystemState", 1
        )[0]

        self.assertIn("manager.stopUpdatingLocation()", stop)
        self.assertIn("stopMotionUpdates()", stop)
        self.assertIn("configureRecordingSystemState(isActive: false)", stop)

    def test_high_frequency_samples_do_not_replace_distance_baseline(self) -> None:
        recorder = source("mini-ninebot/App/NinebotRecordingView.swift")
        short_interval = recorder.split(
            "guard deltaTime >= minimumLocationDeltaTime else", 1
        )[1].split("guard let rawSpeedMPS", 1)[0]

        self.assertNotIn("lastLocation = location", short_interval)

    def test_quality_filters_cover_time_accuracy_stationary_drift_and_jumps(self) -> None:
        recorder = source("mini-ninebot/App/NinebotRecordingView.swift")
        for check in (
            "maximumRecordingLocationAge",
            "maximumHorizontalAccuracy",
            "location.speed >= 0",
            "impliedSpeedMPS",
            "stationarySpeedThresholdMPS",
            "stationaryNoiseRadius",
            "movementNoiseRadius",
        ):
            self.assertIn(check, recorder)


if __name__ == "__main__":
    unittest.main()
