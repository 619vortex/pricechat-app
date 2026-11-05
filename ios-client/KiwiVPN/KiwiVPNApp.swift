import KiwiCore
import SwiftUI

@main
struct KiwiVPNApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView(model: model, connection: model.connection)
                .preferredColorScheme(.dark)
                .tint(KiwiColor.lime)
                .task(id: phase) {
                    // While on screen: the account each minute, and what iOS says about the tunnel every few seconds. The tunnel doesn't depend on this.
                    guard phase == .active else { return }
                    await model.load()
                    var seconds = 0
                    while !Task.isCancelled {
                        await model.connection.reconcile()
                        try? await Task.sleep(nanoseconds: 3_000_000_000)
                        seconds += 3
                        if seconds >= 60 {
                            seconds = 0
                            await model.load()
                        }
                    }
                }
        }
    }
}
