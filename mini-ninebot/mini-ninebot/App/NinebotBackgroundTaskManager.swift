import BackgroundTasks
import Foundation
import WidgetKit

enum NinebotBackgroundTaskManager {
    static let refreshIdentifier = "com.example.NineBotPlus.refresh"
    private static let minimumRefreshInterval: TimeInterval = 15 * 60
    private static var isRegistered = false

    @discardableResult
    static func register() -> Bool {
        guard !isRegistered else { return true }

        isRegistered = BGTaskScheduler.shared.register(forTaskWithIdentifier: refreshIdentifier, using: nil) { task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            handle(refreshTask)
        }
        return isRegistered
    }

    @discardableResult
    static func scheduleRefresh(after interval: TimeInterval = defaultRefreshInterval) -> Bool {
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: refreshIdentifier)

        let request = BGAppRefreshTaskRequest(identifier: refreshIdentifier)
        request.earliestBeginDate = Date().addingTimeInterval(max(interval, minimumRefreshInterval))

        do {
            try BGTaskScheduler.shared.submit(request)
            return true
        } catch {
            // Scheduling can be unavailable in the simulator or when background refresh is disabled.
            return false
        }
    }

    private static func handle(_ task: BGAppRefreshTask) {
        scheduleRefresh()

        let operation = Task {
            await refreshDashboard(source: "Background")
        }

        task.expirationHandler = {
            operation.cancel()
        }

        Task {
            let success = await operation.value
            task.expirationHandler = nil
            task.setTaskCompleted(success: success)
        }
    }

    @discardableResult
    static func refreshDashboard(source: String) async -> Bool {
        let startedAt = Date()
        let store = NinebotSharedStore()
        let cached = store.loadDashboard()
        let sharedConfiguration = store.loadConfiguration() ?? NinebotServerConfiguration(baseURLString: "", bearerToken: "")

        guard sharedConfiguration.isUsable else {
            recordFailure("未配置数据源", source: source, startedAt: startedAt, store: store)
            return false
        }

        let configuration: NinebotServerConfiguration
        do {
            configuration = try NinebotCredentialStore.shared.resolvedConfiguration(from: sharedConfiguration)
        } catch {
            recordFailure("无法读取安全凭据", source: source, startedAt: startedAt, store: store)
            return false
        }

        guard store.loadLoginResult() != nil,
              let sessionToken = configuration.appSessionToken?.trimmingCharacters(in: .whitespacesAndNewlines),
              !sessionToken.isEmpty else {
            recordFailure("未登录，跳过后台刷新", source: source, startedAt: startedAt, store: store)
            return false
        }

        do {
            try Task.checkCancellation()
            let dashboard = try await NinebotServerClient(configuration: configuration)
                .fetchDashboard(selectedSN: cached?.selectedSN)
            try Task.checkCancellation()

            let archivedDashboard = store.saveDashboard(dashboard)
            NinebotChargingLiveActivityManager.sync(with: archivedDashboard)
            store.saveLastAppRefreshEvent(NinebotRefreshEvent(
                source: source,
                operation: "后台刷新",
                startedAt: startedAt,
                endedAt: Date(),
                success: true,
                message: archivedDashboard.primaryVehicle?.vehicle.name
            ))
            WidgetCenter.shared.reloadAllTimelines()
            return true
        } catch is CancellationError {
            recordFailure("后台刷新任务已过期", source: source, startedAt: startedAt, store: store)
            return false
        } catch {
            recordFailure(error.localizedDescription, source: source, startedAt: startedAt, store: store)
            return false
        }
    }

    private static func recordFailure(
        _ message: String,
        source: String,
        startedAt: Date,
        store: NinebotSharedStore
    ) {
        store.saveLastError(message)
        store.saveLastAppRefreshEvent(NinebotRefreshEvent(
            source: source,
            operation: "后台刷新",
            startedAt: startedAt,
            endedAt: Date(),
            success: false,
            message: message
        ))
    }

    private static var defaultRefreshInterval: TimeInterval {
        let state = NinebotSharedStore().loadDashboard()?.primaryVehicle?.state
        if state?.isCharging == true, state?.isFullyCharged != true {
            return 15 * 60
        }
        if state?.isLocked == false || state?.isPoweredOn == true {
            return 20 * 60
        }
        return 30 * 60
    }
}
