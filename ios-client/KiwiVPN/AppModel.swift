import Foundation
import KiwiCore
import SwiftUI

/// A process-wide home for the things both the app and its Shortcuts need.
enum Kiwi {
    static let store = KeychainStore(
        appGroup: Bundle.main.object(forInfoDictionaryKey: "KiwiAppKeychainGroup") as? String ?? "",
        sharedGroup: Bundle.main.object(forInfoDictionaryKey: "KiwiSharedKeychainGroup") as? String ?? "")
    static let driver = NETunnelDriver(extensionID: (Bundle.main.bundleIdentifier ?? "live.pricechat.kiwivpn") + ".tunnel")
    static let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    static let commit = Bundle.main.object(forInfoDictionaryKey: "KiwiBuildCommit") as? String ?? ""
    static let settingsKey = "advanced-settings"
    static let lastServerKey = "last-server"
    static let declarationKey = "data-declaration-accepted"
    static let routingExplainedKey = "iphone-routing-explained"

    /// Settings hold no secret, so they're ordinary preferences.
    static func loadSettings() -> AdvancedSettings {
        guard let data = UserDefaults.standard.data(forKey: settingsKey), let s = try? JSONDecoder().decode(AdvancedSettings.self, from: data) else { return AdvancedSettings() }
        return s
    }

    static func saveSettings(_ settings: AdvancedSettings) {
        UserDefaults.standard.set(try? JSONEncoder().encode(settings), forKey: settingsKey)
    }

    static func lastServer() -> ServerRef? {
        UserDefaults.standard.data(forKey: lastServerKey).flatMap { try? JSONDecoder().decode(ServerRef.self, from: $0) }
    }

    static func saveLastServer(_ server: ServerRef) {
        UserDefaults.standard.set(try? JSONEncoder().encode(server), forKey: lastServerKey)
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var signedIn: Bool
    @Published var signingIn = false
    @Published var who: String?
    @Published var account: Account?
    @Published var checked = false
    @Published var error: String?
    @Published var selected: ServerRef?
    @Published var passport: Passport?
    @Published var missions: [Mission] = []
    @Published var capabilities: [String: Bool] = [:]
    @Published var settings: AdvancedSettings
    @Published var declarationAccepted: Bool
    @Published var routingExplained: Bool
    /// The account's username, from GET /members/me: what account deletion asks the person to type.
    @Published var username: String?
    /// Good news to show on the Welcome screen, such as a finished deletion.
    @Published var notice: String?

    let connection: ConnectionCoordinator
    private let api: KiwiAPI
    private let store: SecretStore
    private let signInService = SignInService()
    private static let refreshAfter: TimeInterval = 7 * 24 * 3600
    private static let refreshedKey = "session-refreshed-at"

    init() {
        guard let api = HTTPKiwiAPI(userAgent: "KiwiVPN-iOS/\(Kiwi.version)") else { fatalError("The base address is TLS") }
        self.api = api
        store = Kiwi.store
        signedIn = Kiwi.store.session() != nil
        settings = Kiwi.loadSettings()
        declarationAccepted = UserDefaults.standard.bool(forKey: Kiwi.declarationKey)
        routingExplained = UserDefaults.standard.bool(forKey: Kiwi.routingExplainedKey)
        connection = ConnectionCoordinator(
            driver: Kiwi.driver,
            source: Provisioner(api: api, store: Kiwi.store),
            ip: { try await api.myIP() },
            settings: { Kiwi.loadSettings() })
        selected = Kiwi.lastServer()
    }

    func acceptDeclaration() {
        UserDefaults.standard.set(true, forKey: Kiwi.declarationKey)
        declarationAccepted = true
    }

    func finishRoutingExplanation() {
        UserDefaults.standard.set(true, forKey: Kiwi.routingExplainedKey)
        routingExplained = true
    }

    /// A failed check keeps the last good answer: a brief network gap, as while the tunnel changes, mustn't take away the countries and Connect. A real sign-out still applies.
    func load() async {
        guard let token = store.session() else { return }
        do {
            try await refreshIfDue(token)
            guard let current = store.session() else { return }
            let fetched = try await api.account(token: current)
            let member = try? await api.me(token: current).member
            account = fetched
            who = member?.displayName ?? member?.username ?? who
            username = member?.username ?? username
            checked = true
            error = nil
            if selected == nil || !fetched.servers.contains(where: { $0.id == selected?.id && $0.online }) {
                selected = suggestedServer(fetched.servers, last: selected?.id)?.ref
            }
            // Optional: production doesn't have this route until it's approved. Any failure keeps what was last shown, and never affects the VPN.
            if let shared = try? await api.unifiedState(token: current, version: Kiwi.version) {
                capabilities = shared.capabilities
                passport = shared.passport ?? passport
                missions = shared.missions
            }
        } catch let e as ApiError where e.signedOut {
            store.setSession(nil)
            signedIn = false
            account = nil
            who = nil
            username = nil
            passport = nil
            checked = true
            error = "Your session ended. Please sign in again."
        } catch {
            checked = true
            if account == nil { self.error = error.localizedDescription }
        }
    }

    /// About weekly, so regular use never reaches the 90-day limit and a copied token soon stops working. Failing changes nothing: the current session still works.
    private func refreshIfDue(_ token: String) async throws {
        let now = Date().timeIntervalSince1970
        let last = UserDefaults.standard.double(forKey: Self.refreshedKey)
        if last == 0 {
            UserDefaults.standard.set(now, forKey: Self.refreshedKey)
        } else if now - last >= Self.refreshAfter {
            do {
                store.setSession(try await api.refresh(token: token))
                UserDefaults.standard.set(now, forKey: Self.refreshedKey)
            } catch let e as ApiError where e.signedOut {
                throw e
            } catch {
                // Kept: the current session still works
            }
        }
    }

    func signIn() async {
        signingIn = true
        error = nil
        defer { signingIn = false }
        do {
            let result = try await signInService.signIn()
            store.setSession(try await api.exchangeCode(code: result.code, verifier: result.verifier))
            UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.refreshedKey)
            signedIn = true
            notice = nil
            await load()
        } catch SignInError.cancelled {
            // The person chose this: nothing to report.
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Only ever on the person's own press. The server allows one per account, and says so if it's been used.
    func startTrial() async {
        guard let token = store.session() else { return }
        do {
            account = try await api.startTrial(token: token)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func connect() async {
        guard let server = selected else { return }
        Kiwi.saveLastServer(server)
        await connection.connect(server)
        await load()
    }

    func disconnect() async {
        await connection.disconnect()
        await load()
    }

    func select(_ server: Server) async {
        selected = server.ref
        Kiwi.saveLastServer(server.ref)
        if connection.state.isActive { await connection.connect(server.ref) }
    }

    func removeDevice(_ id: Int64) async {
        guard let token = store.session() else { return }
        do {
            try await api.deleteDevice(token: token, deviceId: id)
            for (serverId, saved) in store.allDevices() where saved.deviceId == id { store.forgetDevice(serverId: serverId) }
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func updateSettings(_ next: AdvancedSettings) async {
        guard next.problem == nil else { return }
        settings = next
        Kiwi.saveSettings(next)
        if connection.state.isActive {
            await connection.reapply()
        } else if await Kiwi.driver.installedProfile() != nil {
            try? await Kiwi.driver.setOnDemand(next.onDemand != .off, settings: next)
        }
    }

    /// Tunnel down, this phone's devices removed so they don't hold slots, session ended on the server, and keys and the VPN configuration removed here.
    func signOut() async {
        await connection.disconnect()
        if let token = store.session() {
            for saved in store.allDevices().values { try? await api.deleteDevice(token: token, deviceId: saved.deviceId) }
            try? await api.logout(token: token)
        }
        await Kiwi.driver.uninstall()
        store.wipeAccount()
        clearSignedInState()
    }

    private func clearSignedInState() {
        signedIn = false
        account = nil
        who = nil
        username = nil
        passport = nil
        missions = []
        capabilities = [:]
    }

    /// Settings > Delete account. Returns a message to show if it didn't happen. See AccountDeletion for the order of the steps.
    func deleteAccount(typed: String) async -> String? {
        let connection = self.connection
        let deletion = AccountDeletion(api: api, store: store, stopVPN: { await connection.disconnect() }, removeVPN: { await Kiwi.driver.uninstall() })
        do {
            try await deletion.run(typed: typed, username: username)
        } catch let e as ApiError where e.signedOut {
            store.setSession(nil)
            clearSignedInState()
            error = "Your session ended, so the account wasn't deleted. Sign in again, then delete it from Settings."
            return error
        } catch {
            return error.localizedDescription
        }
        // Preferences hold no secret, but they belong to the deleted account. The declaration and the routing explanation are about the app, so they stay read.
        for key in [Kiwi.settingsKey, Kiwi.lastServerKey, Self.refreshedKey] { UserDefaults.standard.removeObject(forKey: key) }
        settings = AdvancedSettings()
        selected = nil
        clearSignedInState()
        error = nil
        notice = "Your Price Chat account has been deleted, and Kiwi VPN has been removed from this iPhone."
        return nil
    }
}
