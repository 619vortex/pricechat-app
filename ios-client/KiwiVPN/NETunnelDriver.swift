import Foundation
import KiwiCore
import NetworkExtension

enum DriverError: LocalizedError {
    case notInstalled
    case refused
    case failed

    var errorDescription: String? {
        switch self {
        case .notInstalled: return "The VPN configuration isn't installed yet. Open Kiwi VPN and press Connect."
        case .refused: return "Kiwi VPN needs your permission to add a VPN configuration. Press Connect, then choose Allow."
        case .failed: return "iOS couldn't start the VPN. Please try again."
        }
    }
}

/// On-demand rules, in the order iOS evaluates them: the first match wins. Only what Apple's NEOnDemandRule offers.
func onDemandRules(_ mode: AdvancedSettings.OnDemand) -> [NEOnDemandRule] {
    func connect(_ type: NEOnDemandRuleInterfaceType) -> NEOnDemandRule {
        let rule = NEOnDemandRuleConnect()
        rule.interfaceTypeMatch = type
        return rule
    }
    let others = NEOnDemandRuleDisconnect()
    others.interfaceTypeMatch = .any
    switch mode {
    case .off: return []
    case .always: return [connect(.any)]
    case .wifi: return [connect(.wiFi), others]
    case .cellular: return [connect(.cellular), others]
    }
}

/// The real tunnel controls: iOS's VPN configuration. The configuration holds no secret, only the details in TunnelProfile. The extension reads the private key from the Keychain.
actor NETunnelDriver: TunnelDriver {
    private let extensionID: String
    private var manager: NETunnelProviderManager?

    init(extensionID: String) { self.extensionID = extensionID }

    private func load() async throws -> NETunnelProviderManager? {
        let all = try await NETunnelProviderManager.loadAllFromPreferences()
        manager = all.first
        return manager
    }

    func install(profile: TunnelProfile, settings: AdvancedSettings) async throws {
        do {
            let m = try await load() ?? NETunnelProviderManager()
            let config = NETunnelProviderProtocol()
            config.providerBundleIdentifier = extensionID
            config.serverAddress = profile.serverName
            config.providerConfiguration = ["profile": try profile.encoded()]
            config.includeAllNetworks = settings.routeAllTraffic
            config.excludeLocalNetworks = settings.allowLocalNetwork
            m.protocolConfiguration = config
            m.localizedDescription = "Kiwi VPN"
            m.isEnabled = true
            m.onDemandRules = onDemandRules(settings.onDemand)
            m.isOnDemandEnabled = settings.onDemand != .off
            try await m.saveToPreferences()
            // Apple's quirk: a configuration has to be loaded again after saving before it can start.
            try await m.loadFromPreferences()
            manager = m
        } catch let e as NEVPNError where e.code == .configurationReadWriteFailed || e.code == .configurationInvalid {
            throw DriverError.refused
        } catch {
            let ns = error as NSError
            // Declining iOS's prompt reports this domain's "permission denied".
            if ns.domain == "NEConfigurationErrorDomain" { throw DriverError.refused }
            throw DriverError.failed
        }
    }

    func start() async throws {
        guard let m = try await load() else { throw DriverError.notInstalled }
        do {
            try m.connection.startVPNTunnel()
        } catch {
            throw DriverError.failed
        }
    }

    func stop() async {
        _ = try? await load()
        manager?.connection.stopVPNTunnel()
    }

    func status() async -> TunnelStatus {
        if manager == nil { _ = try? await load() }
        guard let status = manager?.connection.status else { return .invalid }
        switch status {
        case .invalid: return .invalid
        case .disconnected: return .disconnected
        case .connecting: return .connecting
        case .connected: return .connected
        case .reasserting: return .reasserting
        case .disconnecting: return .disconnecting
        @unknown default: return .invalid
        }
    }

    func stats() async -> TunnelStats? {
        guard let session = manager?.connection as? NETunnelProviderSession, session.status == .connected || session.status == .reasserting else { return nil }
        return await withCheckedContinuation { (done: CheckedContinuation<TunnelStats?, Never>) in
            do {
                try session.sendProviderMessage(Data([0])) { reply in
                    done.resume(returning: reply.flatMap { try? JSONDecoder().decode(TunnelStats.self, from: $0) })
                }
            } catch {
                done.resume(returning: nil)
            }
        }
    }

    func connectedDate() async -> Date? {
        manager?.connection.connectedDate
    }

    func installedProfile() async -> TunnelProfile? {
        guard let m = try? await load(), let config = m.protocolConfiguration as? NETunnelProviderProtocol, let text = config.providerConfiguration?["profile"] as? String else { return nil }
        return TunnelProfile.decoded(from: text)
    }

    func setOnDemand(_ on: Bool, settings: AdvancedSettings) async throws {
        guard let m = try await load() else { return }
        m.onDemandRules = onDemandRules(on ? settings.onDemand : .off)
        m.isOnDemandEnabled = on && settings.onDemand != .off
        try await m.saveToPreferences()
        try await m.loadFromPreferences()
    }

    /// Signing out removes the VPN configuration from Settings too.
    func uninstall() async {
        guard let m = try? await load() else { return }
        m.connection.stopVPNTunnel()
        try? await m.removeFromPreferences()
        manager = nil
    }
}
