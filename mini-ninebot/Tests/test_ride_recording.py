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

    def test_live_speed_prefers_valid_core_location_speed_and_has_safe_fallbacks(self) -> None:
        recorder = source("mini-ninebot/App/NinebotRecordingView.swift")
        speed_candidate = recorder.split("private func speedCandidate", 1)[1].split(
            "private func smoothedSpeed", 1
        )[0]

        self.assertLess(speed_candidate.index("location.speed"), speed_candidate.index("candidate = impliedSpeedMPS"))
        self.assertIn("location.speed >= 0", speed_candidate)
        self.assertIn("location.speedAccuracy <= 6", speed_candidate)
        self.assertIn("impliedSpeedMPS = segmentDistance / deltaTime", speed_candidate)
        self.assertIn("segmentDistance <= movementNoiseRadius ? 0 : impliedSpeedMPS", speed_candidate)
        self.assertIn("isPlausibleSpeedChange", speed_candidate)
        self.assertIn("stationarySpeedSampleCount >= 2", recorder)

    def test_live_speed_label_and_track_points_keep_speed_and_g(self) -> None:
        recording_view = source("mini-ninebot/App/NinebotRecordingView.swift")
        models = source("Shared/NinebotModels.swift")

        self.assertIn('Text("当前速度")', recording_view)
        self.assertIn("minimumFractionDigits: 1", recording_view)
        self.assertIn("let acceleration = motion.userAcceleration", recording_view)
        self.assertIn("maxAccelerationG = max(maxAccelerationG, currentAccelerationG)", recording_view)
        self.assertIn("var speedKmh: Double", models)
        self.assertIn("var accelerationG: Double", models)

    def test_track_playback_uses_a_real_button_and_updates_the_slider_state(self) -> None:
        recording_view = source("mini-ninebot/App/NinebotRecordingView.swift")
        playback = recording_view.split("private struct RecordedRideTrackMap", 1)[1].split(
            "private struct RecordedRideDetailMetrics", 1
        )[0]

        self.assertIn("@State private var isPlaying = false", playback)
        self.assertIn("@State private var playbackTask: Task<Void, Never>?", playback)
        self.assertIn("Button(action: togglePlayback)", playback)
        self.assertIn("playbackProgress = nextProgress", playback)
        self.assertIn("value: $playbackProgress", playback)
        self.assertIn("pausePlayback()", playback)


if __name__ == "__main__":
    unittest.main()
