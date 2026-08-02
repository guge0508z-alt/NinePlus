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
}
