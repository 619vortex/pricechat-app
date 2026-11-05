import Foundation
@testable import KiwiCore

let keyA = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
let keyB = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="
let germany = ServerRef(id: 2, name: "Germany", countryCode: "DE")
let finland = ServerRef(id: 1, name: "Helsinki 1", countryCode: "FI")

func peer(allowedIPs: [String] = ["0.0.0.0/0", "::/0"], host: String = "5.75.10.20", dns: [String] = ["10.66.0.1"]) -> Peer {
    Peer(serverPublicKey: keyA, endpointHost: host, listenPort: 51821, addresses: ["10.66.0.9/32"], dns: dns, allowedIPs: allowedIPs, keepalive: 25)
}

func account(eligible: Bool = true, devices: [Device] = [], maxDevices: Int64 = 3, plan: Plan? = Plan(kind: "trial", daysLeft: 12), trial: Trial? = nil) -> Account {
    Account(eligible: eligible, reason: eligible ? nil : "Your free trial has ended.", maxDevices: maxDevices,
            servers: [Server(id: 1, name: "Helsinki 1", region: "Finland", countryCode: "FI", online: true, recommended: true), Server(id: 2, name: "Germany", region: "Falkenstein", countryCode: "DE", online: true), Server(id: 3, name: "Offline", online: false)],
            devices: devices, plan: plan, trial: trial)
}

final class MemoryStore: SecretStore, @unchecked Sendable {
    var token: String? = "kd.token"
    var devices: [Int64: SavedDevice] = [:]
    var keys: [Int64: String] = [:]
    var id = "abcd1234efgh"
    func session() -> String? { token }
    func setSession(_ token: String?) { self.token = token }
    func device(serverId: Int64) -> SavedDevice? { devices[serverId] }
    func allDevices() -> [Int64: SavedDevice] { devices }
    func privateKey(serverId: Int64) -> String? { keys[serverId] }
    func saveDevice(serverId: Int64, device: SavedDevice, privateKey: String?) throws {
        devices[serverId] = device
        if let privateKey { keys[serverId] = privateKey }
    }
    func forgetDevice(serverId: Int64) {
        devices[serverId] = nil
        keys[serverId] = nil
    }
    func installID() -> String { id }
    func wipeAccount() {
        token = nil
        devices = [:]
        keys = [:]
    }
}

struct FakeKeys: KeyMaker {
    func generate() -> KeyPairText { KeyPairText(privateKey: keyB, publicKey: keyA) }
}

/// The backend's rules that matter to the client: a device limit, and scripted failures.
final class FakeAPI: KiwiAPI, @unchecked Sendable {
    var current = account()
    var calls: [String] = []
    var accountError: ApiError?
    var failuresBeforeAnswer = 0
    var peerAnswer = peer()
    var addError: ApiError?
    var nextId: Int64 = 50

    func account(token: String) async throws -> Account {
        calls.append("account")
        if failuresBeforeAnswer > 0 {
            failuresBeforeAnswer -= 1
            throw ApiError(status: 0, message: unreachableMessage)
        }
        if let accountError { throw accountError }
        return current
    }
    func startTrial(token: String) async throws -> Account { calls.append("trial"); return current }
    func peer(token: String, deviceId: Int64) async throws -> Peer { calls.append("peer \(deviceId)"); return peerAnswer }
    func addDevice(token: String, name: String, serverId: Int64, publicKey: String) async throws -> NewDevice {
        calls.append("add \(serverId) \(publicKey)")
        if let addError { throw addError }
        if current.devices.count >= current.maxDevices { throw ApiError(status: 409, message: "You can have up to \(current.maxDevices) devices. Delete one first.") }
        let device = Device(id: nextId, name: name, serverId: serverId)
        nextId += 1
        current.devices.append(device)
        return NewDevice(device: device, peer: peerAnswer)
    }
    func deleteDevice(token: String, deviceId: Int64) async throws {
        calls.append("delete \(deviceId)")
        current.devices.removeAll { $0.id == deviceId }
    }
    func me(token: String) async throws -> Me { Me(member: nil) }
    func unifiedState(token: String, version: String) async throws -> UnifiedState { UnifiedState() }
    func exchangeCode(code: String, verifier: String) async throws -> String { "kd.token" }
    func refresh(token: String) async throws -> String { "kd.fresh" }
    func logout(token: String) async throws { calls.append("logout") }
    func myIP() async throws -> String { "5.75.10.20" }
}

/// Answers from a script, and records what was asked. Tests the real HTTPKiwiAPI without a network.
final class FakeTransport: HTTPTransport, @unchecked Sendable {
    var status = 200
    var body = "{}"
    var failure: Error?
    var seen: [URLRequest] = []
    func send(_ request: URLRequest) async throws -> (Data, Int) {
        seen.append(request)
        if let failure { throw failure }
        return (Data(body.utf8), status)
    }
}

func fixture(_ name: String) throws -> Data {
    let url = try XCTUnwrapURL(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"))
    return try Data(contentsOf: url)
}

private func XCTUnwrapURL(_ url: URL?) throws -> URL {
    guard let url else { throw NSError(domain: "fixture", code: 1) }
    return url
}
