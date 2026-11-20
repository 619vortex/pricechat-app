import Foundation
import KiwiCore
import NetworkExtension
import WireGuardKit
import os

private let log = Logger(subsystem: "live.pricechat.kiwivpn.tunnel", category: "tunnel")

enum TunnelStartError: String, Error, LocalizedError {
    case noProfile = "The saved VPN configuration can't be read."
    case noKey = "This device's key isn't available. Open Kiwi VPN and connect again."
    case badProfile = "The saved connection details can't be used."
    case startFailed = "WireGuard couldn't start."
    var errorDescription: String? { rawValue }
}

/// The tunnel, in its own process: iOS can start it with no app running, for an on-demand rule. All WireGuard work is in WireGuardKit, from the official wireguard-apple project.
/// Its config comes from the VPN configuration, and its private key from the shared Keychain group. It has no access to the session token, and it makes no requests of its own.
final class PacketTunnelProvider: NEPacketTunnelProvider {
    private lazy var adapter = WireGuardAdapter(with: self) { level, _ in
        // The adapter's messages can hold configuration details, so only their presence is noted, and only for errors.
        if level == .error { log.error("WireGuard reported an error") }
    }

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        let configuration: TunnelConfiguration
        do {
            configuration = try Self.loadConfiguration(from: protocolConfiguration)
        } catch {
            log.error("Start refused: \(error.localizedDescription, privacy: .public)")
            completionHandler(error)
            return
        }
        adapter.start(tunnelConfiguration: configuration) { error in
            if error != nil { log.error("WireGuard didn't start") }
            completionHandler(error == nil ? nil : TunnelStartError.startFailed)
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        adapter.stop { _ in completionHandler() }
    }

    /// Any message gets the statistics, as JSON: wireguard-go's text holds the private key, so it's reduced to these three figures here, and never crosses to the app.
    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)? = nil) {
        guard let completionHandler else { return }
        adapter.getRuntimeConfiguration { text in
            guard let text else {
                completionHandler(nil)
                return
            }
            completionHandler(try? JSONEncoder().encode(TunnelStats.parse(uapi: text)))
        }
    }

    private static func loadConfiguration(from configuration: NEVPNProtocol?) throws -> TunnelConfiguration {
        guard let provider = configuration as? NETunnelProviderProtocol, let text = provider.providerConfiguration?["profile"] as? String, let profile = TunnelProfile.decoded(from: text) else {
            throw TunnelStartError.noProfile
        }
        guard let group = Bundle.main.object(forInfoDictionaryKey: "KiwiSharedKeychainGroup") as? String,
              let raw = wireGuardKeyItem(group: group, serverId: profile.serverId).read(),
              let privateKey = PrivateKey(base64Key: String(decoding: raw, as: UTF8.self)) else {
            throw TunnelStartError.noKey
        }
        guard let publicKey = PublicKey(base64Key: profile.serverPublicKey), let endpoint = Endpoint(from: profile.endpoint) else {
            throw TunnelStartError.badProfile
        }
        var interface = InterfaceConfiguration(privateKey: privateKey)
        interface.addresses = profile.addresses.compactMap { IPAddressRange(from: $0) }
        interface.dns = profile.dns.compactMap { DNSServer(from: $0) }
        interface.mtu = profile.mtu.flatMap { UInt16(exactly: $0) }

        var peer = PeerConfiguration(publicKey: publicKey)
        peer.endpoint = endpoint
        peer.allowedIPs = profile.allowedIPs.compactMap { IPAddressRange(from: $0) }
        peer.persistentKeepAlive = profile.keepalive > 0 ? UInt16(exactly: profile.keepalive) : nil

        // A field that didn't parse must stop the tunnel: fewer routes or no DNS would send traffic around it.
        guard interface.addresses.count == profile.addresses.count, interface.dns.count == profile.dns.count, peer.allowedIPs.count == profile.allowedIPs.count else {
            throw TunnelStartError.badProfile
        }
        return TunnelConfiguration(name: "Kiwi VPN", interface: interface, peers: [peer])
    }
}
