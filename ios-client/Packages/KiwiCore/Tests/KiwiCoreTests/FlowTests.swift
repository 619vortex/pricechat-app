import XCTest
@testable import KiwiCore

final class ProvisionerTests: XCTestCase {
    private let store = MemoryStore()
    private let api = FakeAPI()
    private lazy var p = Provisioner(api: api, store: store, keys: FakeKeys(), pause: {})
    private let settings = AdvancedSettings()
    private var name2: String { deviceName(installID: store.id, serverId: 2) }

    private func get(_ server: ServerRef = germany, keep: Int64? = nil) async throws -> (profile: TunnelProfile, fromCache: Bool) {
        try await p.profile(serverId: server.id, server: server, keepServer: keep, settings: settings)
    }

    func testANewDeviceSendsOnlyThePublicKeyAndKeepsThePrivateOneHere() async throws {
        let out = try await get()
        XCTAssertTrue(api.calls.contains("add 2 \(keyA)"))
        XCTAssertFalse(api.calls.contains { $0.contains(keyB) }, "The private key must never be sent")
        XCTAssertEqual(store.keys[2], keyB)
        XCTAssertFalse(try out.profile.encoded().contains(keyB), "Nor kept in the VPN configuration")
    }

    func testReconnectingReusesTheDevice() async throws {
        _ = try await get()
        for _ in 0..<10 { _ = try await get() }
        XCTAssertEqual(api.calls.filter { $0.hasPrefix("add") }.count, 1)
    }

    func testSwitchingBackAndForthDoesntUseUpTheNewDeviceLimit() async throws {
        for i in 0..<10 { _ = try await get(i % 2 == 0 ? germany : finland) }
        XCTAssertEqual(api.calls.filter { $0.hasPrefix("add") }.count, 2)
    }

    func testADeviceRemovedElsewhereIsReplaced() async throws {
        store.devices[2] = SavedDevice(deviceId: 999, peer: peer())
        store.keys[2] = keyB
        _ = try await get()
        XCTAssertEqual(store.devices[2]?.deviceId, 50)
    }

    func testADeviceWithNoKeyHereIsReplaced() async throws {
        api.current = account(devices: [Device(id: 7, name: name2, serverId: 2)])
        store.devices[2] = SavedDevice(deviceId: 7, peer: peer())
        _ = try await get()
        XCTAssertTrue(api.calls.contains("delete 7"))
        XCTAssertEqual(store.keys[2], keyB)
    }

    func testAReinstalledPhoneClearsItsOwnStaleDevice() async throws {
        api.current = account(devices: [Device(id: 7, name: name2, serverId: 2)])
        _ = try await get()
        XCTAssertTrue(api.calls.contains("delete 7"))
    }

    func testAnotherIPhonesDeviceOnTheSameAccountIsNeverTouched() async throws {
        api.current = account(devices: [Device(id: 8, name: deviceName(installID: "otherphone99", serverId: 2), serverId: 2)])
        _ = try await get()
        XCTAssertFalse(api.calls.contains("delete 8"))
    }

    func testTheDeviceNameFitsTheBackendAndIdentifiesThisInstall() {
        let n = deviceName(installID: "abcd1234efghijkl", serverId: 123_456)
        XCTAssertLessThanOrEqual(n.count, 32)
        XCTAssertEqual(n, "iPhone abcd1234 (123456)")
    }

    func testAFullAccountGivesUpThisPhonesOtherDeviceAndNothingElse() async throws {
        _ = try await get(finland)
        let own = try XCTUnwrap(store.devices[1]?.deviceId)
        api.current.devices += [Device(id: 90, name: "My PC (1)", serverId: 1), Device(id: 91, name: "My PC (2)", serverId: 2)]
        _ = try await get()
        XCTAssertTrue(api.calls.contains("delete \(own)"))
        XCTAssertFalse(api.calls.contains("delete 90"))
        XCTAssertFalse(api.calls.contains("delete 91"))
        XCTAssertNil(store.devices[1])
        XCTAssertNil(store.keys[1])
    }

    func testAFullAccountNeverGivesUpTheDeviceInUse() async throws {
        _ = try await get(finland)
        api.current.devices += [Device(id: 90, name: "My PC (1)", serverId: 1), Device(id: 91, name: "My PC (2)", serverId: 2)]
        do {
            _ = try await get(keep: 1)
            XCTFail("Should report the limit")
        } catch ProfileError.deviceLimit(let tooMany) {
            XCTAssertFalse(tooMany)
        }
        XCTAssertNotNil(store.devices[1])
    }

    func testOtherPeoplesDevicesAloneMeanTheLimitIsReported() async throws {
        api.current = account(devices: [Device(id: 90, name: "PC 1", serverId: 1), Device(id: 91, name: "PC 2", serverId: 2), Device(id: 92, name: "Laptop", serverId: 1)])
        do {
            _ = try await get()
            XCTFail("Should report the limit")
        } catch ProfileError.deviceLimit(let tooMany) {
            XCTAssertFalse(tooMany)
        }
        XCTAssertFalse(api.calls.contains { $0.hasPrefix("delete") })
    }

    func testTooManyNewDevicesSaysSo() async throws {
        api.addError = ApiError(status: 429, message: "Too many")
        do {
            _ = try await get()
            XCTFail("Should report the rate limit")
        } catch ProfileError.deviceLimit(let tooMany) {
            XCTAssertTrue(tooMany)
        }
    }

    func testAnEndedPlanStopsItWithTheServersReason() async throws {
        api.current = account(eligible: false)
        do {
            _ = try await get()
            XCTFail("Should refuse")
        } catch ProfileError.notEntitled(let reason) {
            XCTAssertEqual(reason, "Your free trial has ended.")
        }
    }

    func testAnExpiredSessionClearsTheTokenAndKeepsTheKeys() async throws {
        _ = try await get()
        api.accountError = ApiError(status: 401, message: "Sign in")
        do {
            _ = try await get()
            XCTFail("Should say signed out")
        } catch ProfileError.signedOut {}
        XCTAssertNil(store.token)
        XCTAssertNotNil(store.keys[2])
    }

    func testNotSignedInGoesNowhere() async throws {
        store.token = nil
        do {
            _ = try await get()
            XCTFail("Should say signed out")
        } catch ProfileError.signedOut {}
        XCTAssertTrue(api.calls.isEmpty)
    }

    func testAnOfflineOrUnknownServerIsRefused() async throws {
        do {
            _ = try await get(ServerRef(id: 3, name: "Offline"))
            XCTFail("offline")
        } catch ProfileError.other(let m) { XCTAssertTrue(m.contains("offline")) }
        do {
            _ = try await get(ServerRef(id: 99, name: "Gone"))
            XCTFail("unknown")
        } catch ProfileError.other(let m) { XCTAssertTrue(m.contains("no longer available")) }
    }

    func testANetworkGapJustAfterDisconnectIsWaitedOut() async throws {
        api.failuresBeforeAnswer = 2
        let out = try await get()
        XCTAssertFalse(out.fromCache)
    }

    func testWithNoNetworkItUsesSavedDetails() async throws {
        _ = try await get()
        api.accountError = ApiError(status: 0, message: unreachableMessage)
        let out = try await get()
        XCTAssertTrue(out.fromCache)
    }

    func testWithNoNetworkAndNothingSavedItSaysSo() async throws {
        api.accountError = ApiError(status: 0, message: unreachableMessage)
        do {
            _ = try await get()
            XCTFail("Nothing to use")
        } catch ProfileError.other(let m) { XCTAssertTrue(m.contains("Couldn't reach")) }
    }

    func testAnUntrustworthyAnswerNeverReachesTheTunnel() async throws {
        api.peerAnswer = peer(allowedIPs: ["10.0.0.0/8"])
        do {
            _ = try await get()
            XCTFail("Should refuse")
        } catch ProfileError.badAnswer {}
    }
}

private final class Clock: @unchecked Sendable {
    var time = Date(timeIntervalSince1970: 1_000_000)
}

private final class FakeDriver: TunnelDriver, @unchecked Sendable {
    let clock: Clock
    var tunnel: TunnelStatus = .disconnected
    var answers = true
    var handshake: Date?
    var profile: TunnelProfile?
    var starts = 0
    var stops = 0
    var installs = 0
    var startError: Error?
    var installError: Error?
    var onDemand: [Bool] = []
    var ignoresStop = false

    init(clock: Clock) { self.clock = clock }

    func install(profile: TunnelProfile, settings: AdvancedSettings) async throws {
        if let installError { throw installError }
        installs += 1
        self.profile = profile
    }
    func start() async throws {
        if let startError { throw startError }
        starts += 1
        tunnel = .connected
        handshake = nil
    }
    func stop() async {
        stops += 1
        if !ignoresStop { tunnel = .disconnected }
    }
    func status() async -> TunnelStatus { tunnel }
    func stats() async -> TunnelStats? {
        guard tunnel == .connected || tunnel == .reasserting else { return nil }
        if answers { handshake = clock.time }
        return TunnelStats(lastHandshake: handshake, rxBytes: 100, txBytes: 200)
    }
    func connectedDate() async -> Date? { clock.time }
    func installedProfile() async -> TunnelProfile? { profile }
    func setOnDemand(_ on: Bool, settings: AdvancedSettings) async throws { onDemand.append(on) }
}

private final class FakeSource: ProfileSource, @unchecked Sendable {
    var asked: [(Int64, Int64?)] = []
    var error: Error?
    var limitOnce = false
    func profile(serverId: Int64, server: ServerRef, keepServer: Int64?, settings: AdvancedSettings) async throws -> (profile: TunnelProfile, fromCache: Bool) {
        asked.append((serverId, keepServer))
        if limitOnce {
            limitOnce = false
            throw ProfileError.deviceLimit(tooMany: false)
        }
        if let error { throw error }
        return (try settings.profile(server: server, peer: peer()), false)
    }
}

@MainActor
final class ConnectionTests: XCTestCase {
    private let clock = Clock()
    private lazy var driver = FakeDriver(clock: clock)
    private let source = FakeSource()
    private var ipAnswer = "5.75.10.20"

    private func coordinator(settings: AdvancedSettings = AdvancedSettings()) -> ConnectionCoordinator {
        let clock = self.clock
        let answer = ipAnswer
        return ConnectionCoordinator(driver: driver, source: source, ip: { answer }, settings: { settings }, now: { clock.time }, sleep: { clock.time = clock.time.addingTimeInterval($0) })
    }

    private func isDisconnected(_ s: ConnectionState) -> Bool {
        if case .disconnected = s { return true }
        return false
    }

    func testProtectedNeedsARealHandshakeNotJustIOSSayingConnected() async {
        driver.answers = false
        let c = coordinator()
        await c.connect(germany)
        XCTAssertEqual(driver.starts, 1)
        guard case .failed(let message, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertTrue(message.contains("didn't answer"))
        XCTAssertNotEqual(c.state.word, "Protected")
    }

    func testAServerThatNeverAnswersFailsAndTheTunnelIsRemoved() async {
        driver.answers = false
        let c = coordinator()
        await c.connect(germany)
        XCTAssertEqual(driver.tunnel, .disconnected)
        XCTAssertEqual(driver.stops, 1)
    }

    func testConnectedWithAHandshake() async {
        let c = coordinator()
        await c.connect(germany)
        guard case .connected(let server, let link, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertEqual(server, germany)
        XCTAssertEqual(c.state.word, "Protected")
        XCTAssertEqual(link.endpoint, "5.75.10.20")
        XCTAssertEqual(link.publicIP, "5.75.10.20")
        XCTAssertTrue(link.ipMatchesServer)
    }

    func testAnExitAddressThatIsntTheServersIsntCalledVerified() async {
        ipAnswer = "203.0.113.9"
        let c = coordinator()
        await c.connect(germany)
        XCTAssertEqual(c.state.link?.publicIP, "203.0.113.9")
        XCTAssertEqual(c.state.link?.ipMatchesServer, false)
    }

    func testTwentyConnectDisconnectCyclesLeaveNothingBehind() async {
        let c = coordinator()
        for round in 0..<20 {
            await c.connect(round % 2 == 0 ? germany : finland)
            XCTAssertEqual(c.state.word, "Protected", "cycle \(round)")
            await c.disconnect()
            XCTAssertTrue(isDisconnected(c.state), "cycle \(round)")
            XCTAssertEqual(driver.tunnel, .disconnected)
        }
        XCTAssertEqual(driver.starts, 20)
    }

    func testConnectingToWhatsAlreadyRunningDoesNothing() async {
        let c = coordinator()
        await c.connect(germany)
        await c.connect(germany)
        XCTAssertEqual(driver.starts, 1)
        XCTAssertEqual(source.asked.count, 1)
    }

    func testDisconnectDuringConnectingEndsOff() async {
        driver.answers = false
        let c = coordinator()
        let connecting = Task { await c.connect(germany) }
        await Task.yield()
        await c.disconnect()
        await connecting.value
        XCTAssertTrue(isDisconnected(c.state), "\(c.state)")
        XCTAssertEqual(driver.tunnel, .disconnected)
    }

    func testSwitchingAsksForTheNewServerBeforeTouchingTheOldTunnel() async {
        let c = coordinator()
        await c.connect(germany)
        let stopsBefore = driver.stops
        source.error = ProfileError.other("That server is offline right now.")
        await c.connect(finland)
        XCTAssertEqual(driver.stops, stopsBefore, "A failed fetch must not cost the working tunnel")
        guard case .connected(let server, _, let notice) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertEqual(server, germany)
        XCTAssertTrue(notice?.contains("offline") ?? false)
    }

    func testSwitchingWorks() async {
        let c = coordinator()
        await c.connect(germany)
        await c.connect(finland)
        XCTAssertEqual(c.state.server, finland)
        XCTAssertEqual(c.state.word, "Protected")
        XCTAssertEqual(driver.profile?.serverId, finland.id)
        XCTAssertEqual(source.asked.last?.1, germany.id)
        XCTAssertEqual(driver.starts, 2)
    }

    func testDeviceLimitWhileSwitchingStopsTheOldTunnelThenRetriesKeepingNothing() async {
        let c = coordinator()
        await c.connect(germany)
        source.limitOnce = true
        await c.connect(finland)
        XCTAssertEqual(source.asked[source.asked.count - 2].1, germany.id)
        XCTAssertNil(source.asked.last?.1)
        XCTAssertEqual(c.state.server, finland)
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testReconnectingAfterAFailureWorks() async {
        let c = coordinator()
        source.error = ProfileError.other("nope")
        await c.connect(germany)
        guard case .failed(let message, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertEqual(message, "nope")
        source.error = nil
        await c.connect(germany)
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testDecliningIOSsPromptIsShownAndTheNextTryWorks() async {
        let c = coordinator()
        driver.installError = NSError(domain: "x", code: 1, userInfo: [NSLocalizedDescriptionKey: "Kiwi VPN needs your permission"])
        await c.connect(germany)
        guard case .failed(let message, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertTrue(message.contains("permission"))
        XCTAssertEqual(driver.starts, 0)
        driver.installError = nil
        await c.connect(germany)
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testAStartFailureIsShown() async {
        let c = coordinator()
        driver.startError = NSError(domain: "x", code: 1, userInfo: [NSLocalizedDescriptionKey: "iOS couldn't start the VPN."])
        await c.connect(germany)
        guard case .failed(let message, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertEqual(message, "iOS couldn't start the VPN.")
    }

    func testLosingTheNetworkReadsReconnectingAndRecoversByItself() async {
        let c = coordinator()
        await c.connect(germany)
        driver.answers = false
        clock.time = clock.time.addingTimeInterval(200)
        await c.reconcile()
        guard case .reconnecting = c.state else { return XCTFail("A stale handshake must not read as Protected: \(c.state)") }
        XCTAssertEqual(driver.tunnel, .connected, "The tunnel stays up")
        driver.answers = true
        await c.reconcile()
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testIOSStoppingTheTunnelReadsAsFailedNotAsAnIdleButton() async {
        let c = coordinator()
        await c.connect(germany)
        driver.tunnel = .disconnected
        await c.reconcile()
        guard case .failed(let message, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertTrue(message.contains("iOS stopped"))
        await c.connect(germany)
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testAReopenedAppFindsTheTunnelThatKeptRunning() async throws {
        driver.tunnel = .connected
        driver.profile = try AdvancedSettings().profile(server: germany, peer: peer())
        let c = coordinator()
        await c.reconcile()
        XCTAssertEqual(c.state.word, "Protected")
        XCTAssertEqual(c.state.server?.id, germany.id)
        XCTAssertEqual(driver.starts, 0, "It reads what's there, and starts nothing")
    }

    func testAReopenedAppWithNoHandshakeYetIsntProtectedAndDoesntGetStuck() async throws {
        driver.tunnel = .connected
        driver.answers = false
        driver.profile = try AdvancedSettings().profile(server: germany, peer: peer())
        let c = coordinator()
        await c.reconcile()
        XCTAssertEqual(c.state.word, "Connecting")
        driver.answers = true
        await c.reconcile()
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testAReopenedAppWhoseTunnelNeverCameUpDoesntStayOnConnecting() async throws {
        driver.tunnel = .connected
        driver.answers = false
        driver.profile = try AdvancedSettings().profile(server: germany, peer: peer())
        let c = coordinator()
        await c.reconcile()
        driver.tunnel = .disconnected
        await c.reconcile()
        XCTAssertEqual(c.state.word, "Connection failed")
    }

    func testNothingRunningIsSimplyOff() async {
        let c = coordinator()
        await c.reconcile()
        XCTAssertTrue(isDisconnected(c.state))
    }

    func testDisconnectTurnsOnDemandOffOrIOSWouldReconnectStraightAway() async {
        let c = coordinator()
        await c.connect(germany)
        await c.disconnect()
        XCTAssertEqual(driver.onDemand, [false])
    }

    func testAStopThatNeverFinishesIsReportedNotWaitedOnForever() async {
        let c = coordinator()
        await c.connect(germany)
        driver.ignoresStop = true
        await c.disconnect()
        guard case .failed(let message, _) = c.state else { return XCTFail("\(c.state)") }
        XCTAssertTrue(message.contains("didn't stop"))
    }

    func testNewSettingsRebuildTheInterface() async {
        var s = AdvancedSettings()
        s.mtu = 1380
        let c = coordinator(settings: s)
        await c.connect(germany)
        await c.reapply()
        XCTAssertEqual(driver.starts, 2)
        XCTAssertEqual(driver.profile?.mtu, 1380)
        XCTAssertEqual(c.state.word, "Protected")
    }

    func testReapplyingWhileOffStartsNothing() async {
        let c = coordinator()
        await c.reapply()
        XCTAssertEqual(driver.starts, 0)
    }
}

final class ContractTests: XCTestCase {
    func testEveryFixtureDecodes() throws {
        XCTAssertNoThrow(try JSONDecoder().decode(Account.self, from: try fixture("account")))
        XCTAssertNoThrow(try JSONDecoder().decode(Peer.self, from: try fixture("peer")))
        XCTAssertNoThrow(try JSONDecoder().decode(NewDevice.self, from: try fixture("new_device")))
        XCTAssertNoThrow(try JSONDecoder().decode(UnifiedState.self, from: try fixture("state")))
        XCTAssertNoThrow(try JSONDecoder().decode(Me.self, from: try fixture("me")))
    }

    func testTheFixturePeerPassesTheRules() throws {
        try PeerRules.check(try JSONDecoder().decode(Peer.self, from: try fixture("peer")))
    }

    func testUnknownFieldsAndMissingOptionalsDontBreakIt() throws {
        let a = try JSONDecoder().decode(Account.self, from: Data(#"{"eligible":true,"brand_new":{"x":1}}"#.utf8))
        XCTAssertTrue(a.servers.isEmpty)
        XCTAssertNil(a.plan)
        XCTAssertEqual(try JSONDecoder().decode(UnifiedState.self, from: Data("{}".utf8)), UnifiedState())
    }
}
