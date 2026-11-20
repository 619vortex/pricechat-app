import Foundation

public enum AccountDeletionError: Error, LocalizedError, Equatable, Sendable {
    case notSignedIn
    case usernameUnknown
    case mismatch

    public var errorDescription: String? {
        switch self {
        case .notSignedIn: return "You're not signed in. Sign in first, then delete your account."
        case .usernameUnknown: return "Your username hasn't loaded yet. Check your connection and try again."
        case .mismatch: return "Type your username exactly, as it's shown, to confirm."
        }
    }
}

/// In-app account deletion, App Store Guideline 5.1.1(v). It calls the backend's own DELETE /members/me, the route the website uses, which deletes the whole Price Chat account.
/// The order matters:
/// 1. The tunnel stops first. This phone's requests may be going through it, and step 2 removes its peer from the server, which would cut off step 3.
/// 2. This phone's devices are removed, so their peers leave the servers straight away. Devices elsewhere go with the account, and leave the servers at each server's next sync.
/// 3. The account is deleted. If the server refuses, or can't be reached, nothing more happens here and the person is still signed in.
/// 4. Only after the server confirms: the VPN configuration leaves iOS Settings, and every Keychain item goes.
public struct AccountDeletion: Sendable {
    private let api: KiwiAPI
    private let store: SecretStore
    private let stopVPN: @Sendable () async -> Void
    private let removeVPN: @Sendable () async -> Void

    public init(api: KiwiAPI, store: SecretStore, stopVPN: @escaping @Sendable () async -> Void, removeVPN: @escaping @Sendable () async -> Void) {
        self.api = api; self.store = store; self.stopVPN = stopVPN; self.removeVPN = removeVPN
    }

    /// Exactly the username, as the backend requires. Spaces around it are forgiven, since a keyboard adds them; letter case isn't.
    public static func matches(typed: String, username: String?) -> Bool {
        guard let username, !username.isEmpty else { return false }
        return typed.trimmingCharacters(in: .whitespacesAndNewlines) == username
    }

    /// Throws AccountDeletionError before anything is touched, or the server's ApiError after the tunnel has stopped. Returns only once everything is gone.
    public func run(typed: String, username: String?) async throws {
        guard let token = store.session() else { throw AccountDeletionError.notSignedIn }
        guard let username, !username.isEmpty else { throw AccountDeletionError.usernameUnknown }
        guard Self.matches(typed: typed, username: username) else { throw AccountDeletionError.mismatch }
        await stopVPN()
        for saved in store.allDevices().values.sorted(by: { $0.deviceId < $1.deviceId }) {
            try? await api.deleteDevice(token: token, deviceId: saved.deviceId)
        }
        try await api.deleteAccount(token: token, confirmUsername: username)
        await removeVPN()
        store.wipeAll()
    }
}
