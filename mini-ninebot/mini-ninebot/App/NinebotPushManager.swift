import Foundation
import UIKit
import UserNotifications

enum NinebotPushError: LocalizedError {
    case denied
    case missingToken
    case missingBundleID
    case missingServer

    var errorDescription: String? {
        switch self {
        case .denied:
            return "系统通知权限未开启"
        case .missingToken:
            return "还没有拿到 APNs 设备 Token，请稍后再试"
        case .missingBundleID:
            return "无法读取 App Bundle ID"
        case .missingServer:
            return "请先填写 NinePlus 平台地址和 Token"
        }
    }
}

final class NinebotPushManager: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    static let shared = NinebotPushManager()

    private let store = NinebotSharedStore()

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        NinebotBackgroundTaskManager.register()
        NinebotBackgroundTaskManager.scheduleRefresh()
        UNUserNotificationCenter.current().delegate = self
        NinebotChargingLiveActivityManager.startRemoteTokenObservation()
        Task { await requestAuthorizationOnLaunchAndRegister() }
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        store.savePushDeviceToken(token)
        Task {
            try? await registerStoredTokenWithServer()
            try? await registerStoredLiveActivityTokensWithServer()
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // Registration can fail on simulator or when signing lacks Push Notifications.
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }

    func requestAuthorizationAndRegister() async throws {
        let granted = try await requestAuthorization()
        guard granted else { throw NinebotPushError.denied }
        registerForRemoteNotifications()
        try? await registerStoredLiveActivityTokensWithServer()
    }

    func requestAuthorizationRegisterAndWaitForToken() async throws -> String? {
        let granted = try await requestAuthorization()
        guard granted else { throw NinebotPushError.denied }
        registerForRemoteNotifications()
        try? await registerStoredLiveActivityTokensWithServer()
        return await waitForStoredToken()
    }

    func registerStoredTokenWithServer() async throws {
        guard let token = store.loadPushDeviceToken() else {
            throw NinebotPushError.missingToken
        }
        guard let bundleID = Bundle.main.bundleIdentifier, !bundleID.isEmpty else {
            throw NinebotPushError.missingBundleID
        }
        guard let sharedConfiguration = store.loadConfiguration(), sharedConfiguration.isUsable else {
            throw NinebotPushError.missingServer
        }
        let configuration = try NinebotCredentialStore.shared.resolvedConfiguration(from: sharedConfiguration)
        try await NinebotServerClient(configuration: configuration).registerPushDevice(
            token: token,
            bundleID: bundleID,
            environment: Self.apnsEnvironment
        )
    }

    func registerStoredLiveActivityTokensWithServer() async throws {
        var firstError: Error?

        if let pushToStartToken = store.loadChargingLiveActivityPushToStartToken() {
            do {
                try await registerLiveActivityTokenWithServer(
                    token: pushToStartToken,
                    tokenKind: "push_to_start",
                    activityID: nil,
                    vehicleSN: nil
                )
            } catch {
                firstError = error
            }
        }

        for record in store.loadChargingLiveActivityPushTokenRecords() {
            do {
                try await registerLiveActivityTokenWithServer(
                    token: record.token,
                    tokenKind: "activity",
                    activityID: record.activityID,
                    vehicleSN: record.vehicleSN
                )
            } catch {
                if firstError == nil {
                    firstError = error
                }
            }
        }

        if let firstError {
            throw firstError
        }
    }

    func registerLiveActivityPushTokenWithServer(token: String, activityID: String, vehicleSN: String) async throws {
        store.saveChargingLiveActivityPushToken(token, activityID: activityID, vehicleSN: vehicleSN)
        try await registerLiveActivityTokenWithServer(
            token: token,
            tokenKind: "activity",
            activityID: activityID,
            vehicleSN: vehicleSN
        )
    }

    private func requestAuthorizationOnLaunchAndRegister() async {
        let settings = await notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined:
            guard ((try? await requestAuthorization()) ?? false) else { return }
            registerForRemoteNotifications()
            try? await registerStoredLiveActivityTokensWithServer()
        case .authorized, .provisional, .ephemeral:
            registerForRemoteNotifications()
            try? await registerStoredLiveActivityTokensWithServer()
        default:
            break
        }
    }

    @MainActor
    private func registerForRemoteNotifications() {
        UIApplication.shared.registerForRemoteNotifications()
    }

    private func requestAuthorization() async throws -> Bool {
        try await withCheckedThrowingContinuation { continuation in
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { granted, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: granted)
                }
            }
        }
    }

    private func waitForStoredToken() async -> String? {
        if let token = store.loadPushDeviceToken() {
            return token
        }

        for _ in 0..<20 {
            try? await Task.sleep(nanoseconds: 100_000_000)
            if let token = store.loadPushDeviceToken() {
                return token
            }
        }

        return store.loadPushDeviceToken()
    }

    private func registerLiveActivityTokenWithServer(
        token: String,
        tokenKind: String,
        activityID: String?,
        vehicleSN: String?
    ) async throws {
        guard let bundleID = Bundle.main.bundleIdentifier, !bundleID.isEmpty else {
            throw NinebotPushError.missingBundleID
        }
        guard let sharedConfiguration = store.loadConfiguration(), sharedConfiguration.isUsable else {
            throw NinebotPushError.missingServer
        }
        let configuration = try NinebotCredentialStore.shared.resolvedConfiguration(from: sharedConfiguration)
        try await NinebotServerClient(configuration: configuration).registerLiveActivityToken(
            token: token,
            tokenKind: tokenKind,
            bundleID: bundleID,
            environment: Self.apnsEnvironment,
            deviceToken: store.loadPushDeviceToken(),
            activityID: activityID,
            vehicleSN: vehicleSN
        )
    }

    private func notificationSettings() async -> UNNotificationSettings {
        await withCheckedContinuation { continuation in
            UNUserNotificationCenter.current().getNotificationSettings { settings in
                continuation.resume(returning: settings)
            }
        }
    }

    private static var apnsEnvironment: String {
        if let provisioningEnvironment = Bundle.main.ninebotAPNsEnvironment {
            return provisioningEnvironment
        }
        #if DEBUG
        return "development"
        #else
        return "production"
        #endif
    }
}

private extension Bundle {
    var ninebotAPNsEnvironment: String? {
        guard let url = url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url),
              let profile = String(data: data, encoding: .isoLatin1) ?? String(data: data, encoding: .utf8),
              let keyRange = profile.range(of: "<key>aps-environment</key>") else {
            return nil
        }

        let tail = profile[keyRange.upperBound...]
        guard let valueStart = tail.range(of: "<string>"),
              let valueEnd = tail[valueStart.upperBound...].range(of: "</string>") else {
            return nil
        }

        let value = tail[valueStart.upperBound..<valueEnd.lowerBound]
            .trimmingCharacters(in: .whitespacesAndNewlines)
        switch value {
        case "development", "production":
            return value
        default:
            return nil
        }
    }
}
