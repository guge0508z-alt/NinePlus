import Foundation
import WidgetKit

struct NinebotWidgetEntry: TimelineEntry {
    private static let expirationInterval: TimeInterval = 30 * 60

    var date: Date
    var dashboard: NinebotDashboard
    var errorMessage: String?
    var vehicleImages: [String: Data] = [:]

    func dataIsStale(for snapshot: NinebotVehicleSnapshot) -> Bool {
        if snapshot.state.isStale == true { return true }
        if snapshot.state.updatedAt == .distantPast { return true }
        return date.timeIntervalSince(snapshot.state.updatedAt) > Self.expirationInterval
    }
}

struct NinebotTimelineProvider: TimelineProvider {
#if NINEPLUS_SIDELOAD_FREE
    func placeholder(in context: Context) -> NinebotWidgetEntry {
        testEntry()
    }

    func getSnapshot(in context: Context, completion: @escaping (NinebotWidgetEntry) -> Void) {
        completion(testEntry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NinebotWidgetEntry>) -> Void) {
        let entry = testEntry()
        let nextRefresh = Date().addingTimeInterval(30 * 60)
        completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
    }

    private func testEntry() -> NinebotWidgetEntry {
        var dashboard = NinebotDashboard.preview
        var snapshot = dashboard.vehicles[0]
        snapshot.vehicle.sn = "SIDELOAD-FREE"
        snapshot.vehicle.name = "Nz MIX"
        snapshot.vehicle.model = "Nz MIX"
        snapshot.vehicle.imageURLString = nil
        snapshot.vehicle.raw = nil
        snapshot.state.battery = 58
        snapshot.state.endurance = 33
        snapshot.state.aiEstimatedMileage = nil
        snapshot.state.serverPrediction = nil
        snapshot.state.isCharging = false
        snapshot.state.isPoweredOn = false
        snapshot.state.isLocked = true
        snapshot.state.updatedAt = Date()
        snapshot.state.isStale = false
        dashboard.vehicles = [snapshot]
        dashboard.selectedSN = snapshot.vehicle.sn
        dashboard.updatedAt = snapshot.state.updatedAt
        return NinebotWidgetEntry(date: Date(), dashboard: dashboard, errorMessage: nil)
    }
#else
    func placeholder(in context: Context) -> NinebotWidgetEntry {
        NinebotWidgetEntry(date: Date(), dashboard: .preview, errorMessage: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (NinebotWidgetEntry) -> Void) {
        let store = NinebotSharedStore()
        let dashboard = store.loadDashboard() ?? .preview
        completion(NinebotWidgetEntry(
            date: Date(),
            dashboard: dashboard,
            errorMessage: store.loadLastError(),
            vehicleImages: cachedVehicleImages(for: dashboard, store: store)
        ))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NinebotWidgetEntry>) -> Void) {
        let entry = loadEntry()
        let refreshMinutes = refreshIntervalMinutes(for: entry.dashboard.primaryVehicle?.state)
        let nextRefresh = Calendar.current.date(byAdding: .minute, value: refreshMinutes, to: Date())
            ?? Date().addingTimeInterval(TimeInterval(refreshMinutes * 60))
        completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
    }

    private func refreshIntervalMinutes(for state: NinebotVehicleState?) -> Int {
        guard let state else { return 30 }
        if state.isCharging == true, !state.isFullyCharged { return 3 }
        if state.isLocked == false || state.isPoweredOn == true { return 8 }
        if let battery = state.battery, battery < 20 { return 10 }
        return 20
    }

    private func loadEntry() -> NinebotWidgetEntry {
        let store = NinebotSharedStore()
        let dashboard = store.loadDashboard() ?? .empty
        return NinebotWidgetEntry(
            date: Date(),
            dashboard: dashboard,
            errorMessage: store.loadLastError(),
            vehicleImages: cachedVehicleImages(for: dashboard, store: store)
        )
    }

    private func cachedVehicleImages(for dashboard: NinebotDashboard, store: NinebotSharedStore) -> [String: Data] {
        Dictionary(uniqueKeysWithValues: dashboard.vehicles.compactMap { snapshot in
            guard let data = store.loadVehicleImageData(sn: snapshot.vehicle.sn) else {
                return nil
            }
            return (snapshot.vehicle.sn, data)
        })
    }
#endif
}
