import SwiftUI
import WidgetKit

private struct NinebotTestEntry: TimelineEntry {
    let date: Date
}

private struct NinebotTestProvider: TimelineProvider {
    func placeholder(in context: Context) -> NinebotTestEntry {
        NinebotTestEntry(date: Date())
    }

    func getSnapshot(
        in context: Context,
        completion: @escaping (NinebotTestEntry) -> Void
    ) {
        completion(NinebotTestEntry(date: Date()))
    }

    func getTimeline(
        in context: Context,
        completion: @escaping (Timeline<NinebotTestEntry>) -> Void
    ) {
        completion(
            Timeline(
                entries: [NinebotTestEntry(date: Date())],
                policy: .never
            )
        )
    }
}

private struct NinebotTestWidgetView: View {
    var body: some View {
        Text("Hello Widget")
            .font(.headline)
            .containerBackground(.fill.tertiary, for: .widget)
    }
}

struct NinebotTestWidget: Widget {
    private let kind = "NinebotTestWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: NinebotTestProvider()) { _ in
            NinebotTestWidgetView()
        }
        .configurationDisplayName("Ninebot Test Widget")
        .description("Hello Widget")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

@main
struct NinebotWidgets: WidgetBundle {
    var body: some Widget {
        NinebotTestWidget()
    }
}
