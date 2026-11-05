import Foundation

/// What the Packet Tunnel extension needs in order to run. Nothing here is secret: the private key stays in the Keychain, and the extension reads it from there by `serverId`.
/// It's stored in the VPN configuration's providerConfiguration, as JSON.
public struct TunnelProfile: Codable, Equatable, Sendable {
    public let serverId: Int64
    public let serverName: String
    public let countryCode: String
    public let serverPublicKey: String
    public let endpointHost: String
    public let listenPort: Int
    public let addresses: [String]
    public let dns: [String]
    public let allowedIPs: [String]
    public let keepalive: Int
    public let mtu: Int?

    public var endpoint: String {
        PeerRules.isIPv6(endpointHost) ? "[\(endpointHost)]:\(listenPort)" : "\(endpointHost):\(listenPort)"
    }

    public func encoded() throws -> String {
        String(decoding: try JSONEncoder().encode(self), as: UTF8.self)
    }

    public static func decoded(from text: String) -> TunnelProfile? {
        try? JSONDecoder().decode(TunnelProfile.self, from: Data(text.utf8))
    }
}

/// Settings that really do something. See docs/IOS-RESTRICTIONS.md for those that were left out, and why.
public struct AdvancedSettings: Codable, Equatable, Sendable {
    public enum OnDemand: String, Codable, CaseIterable, Sendable { case off, always, wifi, cellular }

    public var onDemand: OnDemand = .off
    /// Empty: the server's own resolver.
    public var customDNS: [String] = []
    /// nil: the WireGuard library chooses.
    public var mtu: Int?
    /// NEVPNProtocol.includeAllNetworks.
    public var routeAllTraffic = false
    /// NEVPNProtocol.excludeLocalNetworks. Only has an effect with routeAllTraffic.
    public var allowLocalNetwork = true

    public init() {}

    public static let mtuRange = 1280...1420

    public var problem: String? {
        if let mtu, !Self.mtuRange.contains(mtu) { return "MTU must be between \(Self.mtuRange.lowerBound) and \(Self.mtuRange.upperBound)." }
        if customDNS.count > 4 { return "Use up to 4 DNS servers." }
        if !customDNS.allSatisfy({ PeerRules.isIPv4($0) || PeerRules.isIPv6($0) }) { return "A DNS server must be an IP address." }
        return nil
    }

    /// The peer, checked, with these settings applied. The server's routes are never changed, and can't be.
    public func profile(server: ServerRef, peer: Peer) throws -> TunnelProfile {
        try PeerRules.check(peer)
        return TunnelProfile(
            serverId: server.id, serverName: server.name, countryCode: server.countryCode, serverPublicKey: peer.serverPublicKey, endpointHost: peer.endpointHost,
            listenPort: peer.listenPort, addresses: peer.addresses, dns: customDNS.isEmpty ? peer.dns : customDNS, allowedIPs: peer.allowedIPs, keepalive: peer.keepalive, mtu: mtu)
    }
}

/// Crosses from the extension to the app as JSON. The extension reads wireguard-go's runtime configuration itself and sends only this, because that text also holds the private key.
public struct TunnelStats: Codable, Equatable, Sendable {
    public let lastHandshake: Date?
    public let rxBytes: UInt64
    public let txBytes: UInt64
    public init(lastHandshake: Date?, rxBytes: UInt64, txBytes: UInt64) { self.lastHandshake = lastHandshake; self.rxBytes = rxBytes; self.txBytes = txBytes }

    /// Reads wireguard-go's runtime configuration, as WireGuardAdapter.getRuntimeConfiguration returns it. Only these three fields are read, and the text never leaves this function.
    public static func parse(uapi: String) -> TunnelStats {
        var seconds: Int64 = 0, nanos: Int64 = 0, rx: UInt64 = 0, tx: UInt64 = 0
        for line in uapi.split(separator: "\n") {
            guard let at = line.firstIndex(of: "=") else { continue }
            let key = line[..<at], value = line[line.index(after: at)...]
            switch key {
            case "last_handshake_time_sec": seconds = Int64(value) ?? 0
            case "last_handshake_time_nsec": nanos = Int64(value) ?? 0
            case "rx_bytes": rx = UInt64(value) ?? 0
            case "tx_bytes": tx = UInt64(value) ?? 0
            default: break
            }
        }
        let handshake = seconds > 0 ? Date(timeIntervalSince1970: TimeInterval(seconds) + TimeInterval(nanos) / 1_000_000_000) : nil
        return TunnelStats(lastHandshake: handshake, rxBytes: rx, txBytes: tx)
    }
}

/// Gaming Mode's shared shapes. **Off everywhere:** nothing fills these yet, because the backend has no measuring method, and no number may ever be shown without a real measurement behind it. See UNIFIED-ARCHITECTURE.md.
public struct RouteMeasurement: Codable, Equatable, Sendable {
    public let serverId: Int64
    public let latencyMs: Double
    public let jitterMs: Double?
    public let lossPercent: Double?
    public let samples: Int
    public let measuredAt: Date
    public let method: String
}

public struct GameProfile: Codable, Equatable, Identifiable, Sendable {
    public let key: String
    public let name: String
    public var id: String { key }
}

public enum Capability {
    public static let appRouting = "app_routing_enabled" // Android only: per-app VPN on iOS needs MDM. See docs/IOS-RESTRICTIONS.md.
    public static let passport = "passport_enabled"
    public static let referrals = "referrals_enabled"
    public static let gamingMode = "gaming_mode_enabled"
    public static let missions = "vpn_missions_enabled"
}

/// The app's only log. Named events with short values, and every value passes `clean`. Never given a key, a token, a config or an IP address.
public enum Diag {
    public static func clean(_ value: String) -> String {
        if value.count > 40 || value.hasPrefix("kd.") { return "[removed]" }
        if value.contains(":") || value.range(of: #"\d{1,3}(\.\d{1,3}){3}"#, options: .regularExpression) != nil { return "[removed]" }
        if value.range(of: #"[A-Za-z0-9+/_-]{32,}={0,2}"#, options: .regularExpression) != nil { return "[removed]" }
        return value
    }

    public static func line(_ name: String, _ values: [(String, String)] = []) -> String {
        ([name] + values.map { "\($0.0)=\(clean($0.1))" }).joined(separator: " ")
    }
}
