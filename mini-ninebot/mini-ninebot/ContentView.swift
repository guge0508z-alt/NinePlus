//
//  ContentView.swift
//  mini-ninebot
//
//  Created by Jeff He on 2026/7/5.
//

import SwiftUI

private enum NinebotRootTab: Hashable {
    case dashboard
    case trips
    case recording
    case settings
}

struct ContentView: View {
    @StateObject private var model = NinebotViewModel()
    @StateObject private var rideRecorder = NinebotRideRecorder()
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: NinebotRootTab = .dashboard

    var body: some View {
        TabView(selection: $selectedTab) {
            NavigationStack {
                NinebotDashboardView(model: model) {
                    selectedTab = .trips
                }
                    .navigationTitle("")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar(.hidden, for: .navigationBar)
                    .toolbarBackground(.hidden, for: .navigationBar)
            }
            .tabItem {
                Label("车控", systemImage: "dot.circle.and.cursorarrow")
            }
            .tag(NinebotRootTab.dashboard)

            NavigationStack {
                NinebotTripsTabView(model: model)
            }
            .tabItem {
                Label("行程", systemImage: "road.lanes")
            }
            .tag(NinebotRootTab.trips)

            NavigationStack {
                NinebotRecordingView(model: model, recorder: rideRecorder)
            }
            .tabItem {
                Label("记录", systemImage: "gauge.with.dots.needle.67percent")
            }
            .tag(NinebotRootTab.recording)

            NavigationStack {
                NinebotSettingsView(model: model)
                    .navigationTitle(model.hasLoginAccount ? "我的" : "")
                    .toolbar(model.hasLoginAccount ? .visible : .hidden, for: .navigationBar)
            }
            .tabItem {
                Label("我的", systemImage: "person.crop.circle")
            }
            .tag(NinebotRootTab.settings)
        }
        .tint(Color(red: 0.13, green: 0.82, blue: 0.28))
        .task {
            consumePendingAppRoute()
            NinebotBackgroundTaskManager.scheduleRefresh()
            await model.refreshOnLaunchIfPossible()
        }
        .onChange(of: scenePhase) { _, newPhase in
            switch newPhase {
            case .active:
                consumePendingAppRoute()
                NinebotBackgroundTaskManager.scheduleRefresh()
                Task { await model.refreshWhenActiveIfPossible() }
            case .background:
                NinebotBackgroundTaskManager.scheduleRefresh()
            default:
                break
            }
        }
    }

    private func consumePendingAppRoute() {
        guard let route = NinebotSharedStore().consumePendingAppRoute() else { return }
        switch route {
        case .dashboard:
            selectedTab = .dashboard
        case .trips:
            selectedTab = .trips
        case .recording:
            selectedTab = .recording
        case .settings:
            selectedTab = .settings
        }
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
