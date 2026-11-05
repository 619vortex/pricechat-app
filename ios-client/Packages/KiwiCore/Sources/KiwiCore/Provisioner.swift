import CryptoKit
import Foundation

public struct SavedDevice: Codable, Equatable, Sendable {
    public let deviceId: Int64
    public var peer: Peer?
    public init(deviceId: Int64, peer: Peer?) { self.deviceId = deviceId; self.peer = peer }
}

/// What's kept on the phone. KeychainStore is the real one. Private keys are held apart from everything else, because the extension needs them and nothing else.
public protocol SecretStore: AnyObject, Sendable {
    func session() -> String?
    func setSession(_ token: String?)
    func device(serverId: Int64) -> SavedDevice?
    func allDevices() -> [Int64: SavedDevice]
    func privateKey(serverId: Int64) -> String?
    func saveDevice(serverId: Int64, device: SavedDevice, privateKey: String?) throws
    func forgetDevice(serverId: Int64)
    /// Random, made once, and kept across reinstalls. Identifies this phone's own devices. See `deviceName`.
    func installID() -> String
    /// Signed out: the session and keys go.
    func wipeAccount()
}

public struct KeyPairText: Equatable, Sendable {
    public let privateKey: String
    public let publicKey: String
    public init(privateKey: String, publicKey: String) { self.privateKey = privateKey; self.publicKey = publicKey }
}

public protocol KeyMaker: Sendable {
    func generate() -> KeyPairText
}

/// X25519 from Apple's CryptoKit, with its secure random source. The main app makes the key this way because it doesn't link WireGuard's Go library: only the extension does.
public struct CryptoKitKeyMaker: KeyMaker {
    public init() {}
    public func generate() -> KeyPairText { Self.pair(raw: Curve25519.KeyAgreement.PrivateKey().rawRepresentation) }

    /// With the clamping every WireGuard key has. X25519 clamps internally too, so the public half is the same either way: the RFC 7748 test checks this.
    public static func pair(raw: Data) -> KeyPairText {
        var bytes = [UInt8](raw)
        bytes[0] &= 248
        bytes[31] &= 127
        bytes[31] |= 64
        let clamped = Data(bytes)
        guard let key = try? Curve25519.KeyAgreement.PrivateKey(rawRepresentation: clamped) else { fatalError("32 bytes is always a valid X25519 key") }
        return KeyPairText(privateKey: clamped.base64EncodedString(), publicKey: key.publicKey.rawRepresentation.base64EncodedString())
    }
}

public enum ProfileError: Error, LocalizedError, Equatable {
    case signedOut
    case notEntitled(String?)
    case deviceLimit(tooMany: Bool)
    case other(String)
    case badAnswer

    public var errorDescription: String? {
        switch self {
        case .signedOut: return "Please sign in again."
        case .notEntitled(let reason): return reason ?? "Your account doesn't include Kiwi VPN right now."
        case .deviceLimit(let tooMany): return tooMany ? "Too many new devices. Please wait a few minutes and try again." : "You've used all your devices. Remove one under Account, or at pricechat.live/vpn, then try again."
        case .other(let message): return message
        case .badAnswer: return "The server sent connection details the app can't trust, so it didn't connect. Please try again later."
        }
    }
}

public protocol ProfileSource: Sendable {
    /// `keepServer`: the server the running tunnel uses, whose device must not be removed to make room.
    func profile(serverId: Int64, server: ServerRef, keepServer: Int64?, settings: AdvancedSettings) async throws -> (profile: TunnelProfile, fromCache: Bool)
}

/// Up to the backend's 32 characters. The random part stops the cleanup in `register` from ever taking another iPhone's device on the same account.
public func deviceName(installID: String, serverId: Int64) -> String {
    "iPhone \(installID.prefix(8)) (\(serverId))"
}

/// From "choose a server" to a config, reusing this phone's device on that server, or making a key and registering one. The private key is made here, and only its public half is ever sent.
///
/// Why a device stays after switching away: the backend allows 5 new devices, then one every 10 minutes, so a new device per switch would soon fail. Why one is sometimes removed: each server needs its own device,
/// and an account has 3 by default. Only when the account is full does this phone give up its own device on another server. See docs/API-CONTRACT.md.
public final class Provisioner: ProfileSource, @unchecked Sendable {
    private let api: KiwiAPI
    private let store: SecretStore
    private let keys: KeyMaker
    private let pause: @Sendable () async -> Void

    public init(api: KiwiAPI, store: SecretStore, keys: KeyMaker = CryptoKitKeyMaker(), pause: @escaping @Sendable () async -> Void = { try? await Task.sleep(nanoseconds: 1_500_000_000) }) {
        self.api = api; self.store = store; self.keys = keys; self.pause = pause
    }

    public func profile(serverId: Int64, server: ServerRef, keepServer: Int64?, settings: AdvancedSettings) async throws -> (profile: TunnelProfile, fromCache: Bool) {
        guard let token = store.session() else { throw ProfileError.signedOut }
        let account: Account
        do {
            account = try await withRetry { try await self.api.account(token: token) }
        } catch let e as ApiError {
            if e.unreachable {
                if let cached = cached(server: server, settings: settings) { return (cached, true) }
                throw ProfileError.other(e.message)
            }
            if e.signedOut {
                store.setSession(nil)
                throw ProfileError.signedOut
            }
            throw ProfileError.other(e.message)
        }
        guard account.eligible else { throw ProfileError.notEntitled(account.reason) }
        guard let live = account.servers.first(where: { $0.id == serverId }) else { throw ProfileError.other("That server is no longer available. Please choose another.") }
        guard live.online else { throw ProfileError.other("That server is offline right now. Please choose another.") }

        if let saved = store.device(serverId: serverId) {
            if account.devices.contains(where: { $0.id == saved.deviceId }), store.privateKey(serverId: serverId) != nil {
                do {
                    let peer = try await withRetry { try await self.api.peer(token: token, deviceId: saved.deviceId) }
                    return (try build(serverId: serverId, deviceId: saved.deviceId, privateKey: nil, server: server, peer: peer, settings: settings), false)
                } catch let e as ApiError {
                    if e.unreachable {
                        if let cached = cached(server: server, settings: settings) { return (cached, true) }
                        throw ProfileError.other(e.message)
                    }
                    if e.signedOut {
                        store.setSession(nil)
                        throw ProfileError.signedOut
                    }
                    throw ProfileError.other(e.message)
                }
            }
            store.forgetDevice(serverId: serverId) // removed on the website or from another device: this key no longer works
        }
        return (try await register(token: token, account: account, serverId: serverId, server: server, keepServer: keepServer, settings: settings), false)
    }

    private func register(token: String, account: Account, serverId: Int64, server: ServerRef, keepServer: Int64?, settings: AdvancedSettings) async throws -> TunnelProfile {
        let name = deviceName(installID: store.installID(), serverId: serverId)
        do {
            // A device with this phone's name whose key is gone can never connect again, so it shouldn't hold a slot.
            if let stale = account.devices.first(where: { $0.serverId == serverId && $0.name == name }) {
                try await api.deleteDevice(token: token, deviceId: stale.id)
            }
            let pair = keys.generate()
            let added: NewDevice
            do {
                added = try await api.addDevice(token: token, name: name, serverId: serverId, publicKey: pair.publicKey)
            } catch let e as ApiError where e.status == 409 {
                guard try await makeRoom(token: token, serverId: serverId, keepServer: keepServer) else { throw e }
                added = try await api.addDevice(token: token, name: name, serverId: serverId, publicKey: pair.publicKey)
            }
            return try build(serverId: serverId, deviceId: added.device.id, privateKey: pair.privateKey, server: server, peer: added.peer, settings: settings)
        } catch let e as ApiError {
            switch e.status {
            case 401:
                store.setSession(nil)
                throw ProfileError.signedOut
            case 409: throw ProfileError.deviceLimit(tooMany: false)
            case 429: throw ProfileError.deviceLimit(tooMany: true)
            default: throw ProfileError.other(e.message)
            }
        }
    }

    /// Gives up this phone's own device on another server. True if one went.
    private func makeRoom(token: String, serverId: Int64, keepServer: Int64?) async throws -> Bool {
        guard let spare = store.allDevices().sorted(by: { $0.key < $1.key }).first(where: { $0.key != serverId && $0.key != keepServer }) else { return false }
        try await api.deleteDevice(token: token, deviceId: spare.value.deviceId)
        store.forgetDevice(serverId: spare.key)
        return true
    }

    private func build(serverId: Int64, deviceId: Int64, privateKey: String?, server: ServerRef, peer: Peer, settings: AdvancedSettings) throws -> TunnelProfile {
        let profile: TunnelProfile
        do {
            profile = try settings.profile(server: server, peer: peer)
        } catch is BadPeer {
            throw ProfileError.badAnswer
        }
        do {
            try store.saveDevice(serverId: serverId, device: SavedDevice(deviceId: deviceId, peer: peer), privateKey: privateKey)
        } catch {
            throw ProfileError.other("Couldn't save the device's key on this phone.")
        }
        return profile
    }

    /// Price Chat can't be reached: use the details saved the last time. It can't skip a check: the server only accepts this key while the account is entitled to it, and otherwise the handshake never completes.
    private func cached(server: ServerRef, settings: AdvancedSettings) -> TunnelProfile? {
        guard store.privateKey(serverId: server.id) != nil, let peer = store.device(serverId: server.id)?.peer else { return nil }
        return try? settings.profile(server: server, peer: peer)
    }

    /// A request that never got an answer is tried again, since the network takes a moment to settle after the tunnel changes. Any answer from Price Chat stands.
    private func withRetry<T>(_ call: @escaping () async throws -> T) async throws -> T {
        for _ in 0..<2 {
            do {
                return try await call()
            } catch let e as ApiError where e.unreachable {
                await pause()
            }
        }
        return try await call()
    }
}
