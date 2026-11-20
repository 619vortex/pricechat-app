import AppIntents
import Foundation
import KiwiCore

// Shortcuts: "Connect Kiwi VPN" and "Disconnect Kiwi VPN". A person can run these from Siri, or from their own automations, such as arriving somewhere or opening an app. They use the configuration already
// installed and don't talk to Price Chat, so the first connection has to be made in the app. A Shortcut can't prove a tunnel is up by itself: the result says only that the request was made.

struct ConnectKiwiVPN: AppIntent {
    static let title: LocalizedStringResource = "Connect Kiwi VPN"
    static let description = IntentDescription("Asks iOS to start Kiwi VPN using the last country you connected to.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard await Kiwi.driver.installedProfile() != nil else { throw DriverError.notInstalled }
        try await Kiwi.driver.start()
        return .result(dialog: "Asked iOS to start Kiwi VPN. Open the app to see its status.")
    }
}

struct DisconnectKiwiVPN: AppIntent {
    static let title: LocalizedStringResource = "Disconnect Kiwi VPN"
    static let description = IntentDescription("Stops Kiwi VPN, and turns off on-demand rules so it doesn't start again by itself.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try? await Kiwi.driver.setOnDemand(false, settings: Kiwi.loadSettings())
        await Kiwi.driver.stop()
        return .result(dialog: "Asked iOS to stop Kiwi VPN.")
    }
}

struct KiwiShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: ConnectKiwiVPN(), phrases: ["Connect \(.applicationName)"], shortTitle: "Connect", systemImageName: "lock.shield")
        AppShortcut(intent: DisconnectKiwiVPN(), phrases: ["Disconnect \(.applicationName)"], shortTitle: "Disconnect", systemImageName: "lock.open")
    }
}
