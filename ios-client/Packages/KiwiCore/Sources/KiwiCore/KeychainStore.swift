import Foundation
import Security

/// The Keychain, this device only, with no iCloud sync and no transfer to a new phone. AfterFirstUnlock, because an on-demand tunnel may start while the phone is locked.
/// `group` must be listed in the target's keychain-access-groups entitlement.
public struct KeychainItem: Sendable {
    public let group: String
    public let service: String
    public let account: String

    public init(group: String, service: String, account: String) { self.group = group; self.service = service; self.account = account }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrAccessGroup as String: group, kSecAttrService as String: service, kSecAttrAccount as String: account, kSecUseDataProtectionKeychain as String: true]
    }

    public func read() -> Data? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    public func write(_ data: Data) throws {
        let update: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            add.merge(update) { _, new in new }
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }

    public func delete() {
        SecItemDelete(query as CFDictionary)
    }
}

public let wireGuardKeyService = "live.pricechat.kiwivpn.wg"

/// The extension's copy of this: the private key for a server, and nothing else is in that group.
public func wireGuardKeyItem(group: String, serverId: Int64) -> KeychainItem {
    KeychainItem(group: group, service: wireGuardKeyService, account: "server-\(serverId)")
}

/// `appGroup` holds the session and device list, and only the app can read it. `sharedGroup` holds the WireGuard keys, for the extension. So the extension never has the session.
public final class KeychainStore: SecretStore, @unchecked Sendable {
    private let appGroup: String
    private let sharedGroup: String
    private let lock = NSLock()

    public init(appGroup: String, sharedGroup: String) {
        self.appGroup = appGroup
        self.sharedGroup = sharedGroup
    }

    private func item(_ name: String) -> KeychainItem { KeychainItem(group: appGroup, service: "live.pricechat.kiwivpn.app", account: name) }

    public func session() -> String? {
        item("session").read().map { String(decoding: $0, as: UTF8.self) }
    }

    public func setSession(_ token: String?) {
        if let token {
            try? item("session").write(Data(token.utf8))
        } else {
            item("session").delete()
        }
    }

    private func devices() -> [Int64: SavedDevice] {
        guard let data = item("devices").read() else { return [:] }
        return (try? JSONDecoder().decode([Int64: SavedDevice].self, from: data)) ?? [:]
    }

    private func store(_ devices: [Int64: SavedDevice]) throws {
        try item("devices").write(JSONEncoder().encode(devices))
    }

    public func device(serverId: Int64) -> SavedDevice? {
        lock.lock(); defer { lock.unlock() }
        return devices()[serverId]
    }

    public func allDevices() -> [Int64: SavedDevice] {
        lock.lock(); defer { lock.unlock() }
        return devices()
    }

    public func privateKey(serverId: Int64) -> String? {
        wireGuardKeyItem(group: sharedGroup, serverId: serverId).read().map { String(decoding: $0, as: UTF8.self) }
    }

    public func saveDevice(serverId: Int64, device: SavedDevice, privateKey: String?) throws {
        lock.lock(); defer { lock.unlock() }
        if let privateKey { try wireGuardKeyItem(group: sharedGroup, serverId: serverId).write(Data(privateKey.utf8)) }
        var all = devices()
        all[serverId] = device
        try store(all)
    }

    public func forgetDevice(serverId: Int64) {
        lock.lock(); defer { lock.unlock() }
        wireGuardKeyItem(group: sharedGroup, serverId: serverId).delete()
        var all = devices()
        all[serverId] = nil
        try? store(all)
    }

    public func installID() -> String {
        lock.lock(); defer { lock.unlock() }
        if let data = item("install-id").read(), !data.isEmpty { return String(decoding: data, as: UTF8.self) }
        let made = SignInAttempt.randomText(bytes: 12)?.replacingOccurrences(of: "-", with: "").replacingOccurrences(of: "_", with: "") ?? "device"
        try? item("install-id").write(Data(made.utf8))
        return made
    }

    public func wipeAccount() {
        lock.lock(); defer { lock.unlock() }
        for id in devices().keys { wireGuardKeyItem(group: sharedGroup, serverId: id).delete() }
        item("devices").delete()
        item("session").delete()
    }
}
