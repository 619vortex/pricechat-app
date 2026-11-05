import CryptoKit
import XCTest
@testable import KiwiCore

final class SignInTests: XCTestCase {
    func testChallengeMatchesTheRFC7636Vector() {
        XCTAssertEqual(SignInAttempt.challenge(of: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
    }

    func testAnAttemptHasWhatTheWebsitePageRequires() throws {
        let a = try XCTUnwrap(SignInAttempt.create())
        XCTAssertEqual(a.verifier.count, 43)
        XCTAssertEqual(a.challenge.count, 43)
        XCTAssertTrue((16...64).contains(a.state.count))
        XCTAssertEqual(SignInAttempt.challenge(of: a.verifier), a.challenge)
        let url = try XCTUnwrap(a.pageURL(base: "https://x", port: 50123)).absoluteString
        XCTAssertEqual(url, "https://x/vpn/desktop?challenge=\(a.challenge)&port=50123&state=\(a.state)&client=ios")
        XCTAssertFalse(url.contains(a.verifier), "The verifier stays in the app")
    }

    func testAttemptsDiffer() throws {
        let a = try XCTUnwrap(SignInAttempt.create()), b = try XCTUnwrap(SignInAttempt.create())
        XCTAssertNotEqual(a.verifier, b.verifier)
        XCTAssertNotEqual(a.state, b.state)
    }

    func testOnlyOurLoopbackCallbackWithOurStateGivesACode() {
        let code = String(repeating: "c", count: 43)
        XCTAssertEqual(readLoopbackCallback(requestLine: "GET /callback?code=\(code)&state=abc HTTP/1.1", state: "abc"), code)
        XCTAssertEqual(readLoopbackCallback(requestLine: "GET /callback?state=abc&code=\(code) HTTP/1.1", state: "abc"), code)
        for bad in ["GET /callback?code=\(code)&state=other HTTP/1.1", "GET /callback?code=\(code) HTTP/1.1", "POST /callback?code=\(code)&state=abc HTTP/1.1", "GET /other?code=\(code)&state=abc HTTP/1.1", "GET /callback?code=short&state=abc HTTP/1.1", "", "GET"] {
            XCTAssertNil(readLoopbackCallback(requestLine: bad, state: "abc"), bad)
        }
    }

    func testOnlyOurSchemeCallbackWithOurStateGivesACode() throws {
        let code = String(repeating: "k", count: 43)
        XCTAssertEqual(readSchemeCallback(url: try XCTUnwrap(URL(string: "kiwivpn://callback?code=\(code)&state=abc")), state: "abc"), code)
        for bad in ["kiwivpn://callback?code=\(code)&state=other", "kiwivpn://callback?code=\(code)", "other://callback?code=\(code)&state=abc", "kiwivpn://signed-in?code=\(code)&state=abc", "kiwivpn://callback/x?code=\(code)&state=abc", "kiwivpn://callback?code=short&state=abc", "https://evil.example/callback?code=\(code)&state=abc", "kiwivpn://signed-in"] {
            XCTAssertNil(readSchemeCallback(url: try XCTUnwrap(URL(string: bad)), state: "abc"), bad)
        }
    }

    func testTheLoopbackReplyNeverEchoesACodeAndEndsTheSheet() {
        let ok = String(decoding: loopbackReply(ok: true), as: UTF8.self)
        XCTAssertTrue(ok.hasPrefix("HTTP/1.1 302"))
        XCTAssertTrue(ok.contains("Location: kiwivpn://signed-in\r\n"))
        XCTAssertFalse(ok.contains("code"))
        let refused = String(decoding: loopbackReply(ok: false), as: UTF8.self)
        XCTAssertTrue(refused.hasPrefix("HTTP/1.1 400"))
        XCTAssertFalse(refused.contains("kiwivpn"))
        let body = refused.components(separatedBy: "\r\n\r\n")[1]
        XCTAssertTrue(refused.contains("Content-Length: \(body.utf8.count)\r\n"), "A wrong length would hang the browser")
    }
}

final class APITests: XCTestCase {
    private func api(_ t: FakeTransport) throws -> HTTPKiwiAPI { try XCTUnwrap(HTTPKiwiAPI(base: "https://pricechat.test", transport: t)) }

    func testOnlyTLSIsAccepted() {
        XCTAssertNil(HTTPKiwiAPI(base: "http://pricechat.live"))
    }

    func testTheRealAccountReplyParsesAndTheSessionGoesOnlyAsACookie() async throws {
        let t = FakeTransport()
        t.body = String(decoding: try fixture("account"), as: UTF8.self)
        let a = try await api(t).account(token: "kd.secret")
        XCTAssertTrue(a.eligible)
        XCTAssertEqual(a.plan?.daysLeft, 12)
        XCTAssertEqual(a.servers.first?.countryCode, "DE")
        XCTAssertEqual(a.devices.first?.serverId, 2)
        let r = try XCTUnwrap(t.seen.first)
        XCTAssertEqual(r.url?.absoluteString, "https://pricechat.test/api/members/vpn")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Cookie"), "pc_session=kd.secret")
        XCTAssertFalse(r.httpShouldHandleCookies, "Otherwise URLSession drops the Cookie header")
        XCTAssertFalse(r.url?.absoluteString.contains("secret") ?? true)
    }

    func testAddingADeviceSendsWhatTheBackendRequiresAndNoPrivateKey() async throws {
        let t = FakeTransport()
        t.body = String(decoding: try fixture("new_device"), as: UTF8.self)
        let d = try await api(t).addDevice(token: "t", name: "iPhone x (2)", serverId: 2, publicKey: keyA)
        XCTAssertEqual(d.peer.listenPort, 51821)
        let sent = try JSONSerialization.jsonObject(with: try XCTUnwrap(t.seen.first?.httpBody)) as? [String: Any]
        XCTAssertEqual(Set(try XCTUnwrap(sent).keys), ["name", "server_id", "public_key"])
        XCTAssertEqual(sent?["public_key"] as? String, keyA)
    }

    func testTheTokenSwapHasNoCookie() async throws {
        let t = FakeTransport()
        t.body = #"{"token":"kd.abc","expires_in_days":90}"#
        let token = try await api(t).exchangeCode(code: "code", verifier: "verifier")
        XCTAssertEqual(token, "kd.abc")
        XCTAssertNil(t.seen.first?.value(forHTTPHeaderField: "Cookie"))
    }

    func testTheSharedStateParses() async throws {
        let t = FakeTransport()
        t.body = String(decoding: try fixture("state"), as: UTF8.self)
        let s = try await api(t).unifiedState(token: "t", version: "0.1.0")
        XCTAssertEqual(s.passport?.xp, 2500)
        XCTAssertEqual(s.passport?.rank.current.name, "Warrior")
        XCTAssertEqual(s.passport?.rank.xpToNext, 500)
        XCTAssertEqual(s.passport?.standing?.position, 84)
        XCTAssertEqual(s.passport?.referral?.code, "kiwi123456")
        XCTAssertEqual(s.capabilities["auto_call_enabled"], false)
        let query = t.seen.first?.url?.query ?? ""
        XCTAssertTrue(query.contains("platform=ios"))
        XCTAssertTrue(query.contains("version=0.1.0"))
    }

    func testTheBackendsMessageIsShownAndOtherErrorShapesStillGiveOne() async throws {
        let t = FakeTransport()
        t.status = 409
        t.body = #"{"detail":"You can have up to 3 devices. Delete one first."}"#
        do {
            _ = try await api(t).addDevice(token: "t", name: "n", serverId: 1, publicKey: keyA)
            XCTFail("Should have thrown")
        } catch let e as ApiError {
            XCTAssertEqual(e.status, 409)
            XCTAssertEqual(e.message, "You can have up to 3 devices. Delete one first.")
        }
        for body in [#"{"detail":[{"loc":["x"]}]}"#, "<html>", ""] {
            t.status = 500
            t.body = body
            do {
                _ = try await api(t).account(token: "t")
                XCTFail("Should have thrown")
            } catch let e as ApiError {
                XCTAssertEqual(e.message, "Price Chat answered 500.")
            }
        }
    }

    func testNoAnswerIsUnreachableAnd401IsSignedOut() async throws {
        let t = FakeTransport()
        t.failure = URLError(.notConnectedToInternet)
        do {
            _ = try await api(t).account(token: "t")
            XCTFail("Should have thrown")
        } catch let e as ApiError {
            XCTAssertTrue(e.unreachable)
        }
        t.failure = nil
        t.status = 401
        t.body = #"{"detail":"Sign in"}"#
        do {
            _ = try await api(t).account(token: "t")
            XCTFail("Should have thrown")
        } catch let e as ApiError {
            XCTAssertTrue(e.signedOut)
        }
    }

    func testAGarbledSuccessIsAnErrorNotACrash() async throws {
        let t = FakeTransport()
        t.body = "not json"
        do {
            _ = try await api(t).account(token: "t")
            XCTFail("Should have thrown")
        } catch let e as ApiError {
            XCTAssertTrue(e.message.contains("can't read"))
        }
    }
}

final class AccountStatusTests: XCTestCase {
    func testAnActiveTrialShowsItsTimeAndOffersNoNewOne() {
        let s = AccountStatus.of(account(trial: Trial(available: false, days: 30)))
        XCTAssertEqual(s.kind, .trial)
        XCTAssertEqual(s.label, "Free trial: 12 days left")
        XCTAssertTrue(s.canConnect)
        XCTAssertFalse(s.canStartTrial)
    }

    func testSigningInNeverStartsATrialItOnlyOffersWhenTheServerDoes() {
        let offered = AccountStatus.of(account(eligible: false, plan: Plan(kind: "not_activated"), trial: Trial(available: true, days: 30)))
        XCTAssertEqual(offered.kind, .needsTrial)
        XCTAssertTrue(offered.canStartTrial)
        XCTAssertFalse(offered.canConnect)
        XCTAssertFalse(AccountStatus.of(account(eligible: false, plan: Plan(kind: "not_activated"), trial: Trial(available: false, days: 30))).canStartTrial)
    }

    func testEndedClosedBlockedAndUnavailableCantConnect() {
        for (kind, want) in [("expired", StatusKind.expired), ("closed", .closed), ("blocked", .blocked)] {
            let s = AccountStatus.of(account(eligible: false, plan: Plan(kind: kind)))
            XCTAssertEqual(s.kind, want)
            XCTAssertFalse(s.canConnect)
            XCTAssertFalse(s.canStartTrial)
        }
        XCTAssertEqual(AccountStatus.of(nil).kind, .unavailable)
    }

    func testPaidAndSpecialKindsAreActive() {
        XCTAssertEqual(AccountStatus.of(account(plan: Plan(kind: "paid", daysLeft: 3))).kind, .paid)
        for k in ["owner", "royal", "free", "granted"] { XCTAssertEqual(AccountStatus.of(account(plan: Plan(kind: k))).kind, .unlimited) }
    }

    func testAKindThisVersionDoesntKnowFollowsTheServer() {
        XCTAssertTrue(AccountStatus.of(account(eligible: true, plan: Plan(kind: "new"))).canConnect)
        XCTAssertFalse(AccountStatus.of(account(eligible: false, plan: Plan(kind: "new"))).canConnect)
    }

    func testTheServerSuggestedFirst() {
        let all = account().servers
        XCTAssertEqual(suggestedServer(all, last: 2)?.id, 2)
        XCTAssertEqual(suggestedServer(all, last: 3)?.id, 1, "The last one is offline")
        XCTAssertEqual(suggestedServer(all, last: nil)?.id, 1)
        XCTAssertNil(suggestedServer([], last: nil))
    }
}

final class PeerRulesTests: XCTestCase {
    private func rejected(_ p: Peer, line: UInt = #line) {
        XCTAssertThrowsError(try PeerRules.check(p), line: line)
    }

    func testTheRealShapePasses() throws {
        try PeerRules.check(peer())
        try PeerRules.check(Peer(serverPublicKey: keyA, endpointHost: "5.75.10.20", listenPort: 51821, addresses: ["10.66.0.9/32", "fd00::9/128"], dns: ["10.66.0.1"], allowedIPs: ["0.0.0.0/0", "::/0"], keepalive: 25))
        try PeerRules.check(peer(host: "de.pricechat.example"))
    }

    func testRoutesMustBeExactlyTheFullTunnel() {
        rejected(peer(allowedIPs: ["0.0.0.0/0"])) // IPv6 would go around the tunnel
        rejected(peer(allowedIPs: ["::/0"]))
        rejected(peer(allowedIPs: ["0.0.0.0/1", "128.0.0.0/1", "::/0"]))
        rejected(peer(allowedIPs: []))
    }

    func testNoDNSOrAnInjectedOneIsRefused() {
        rejected(peer(dns: []))
        rejected(peer(dns: ["dns.example.com"]))
        rejected(peer(dns: ["1.1.1.1\nPostUp = evil"]))
    }

    func testBadKeysAreRefused() {
        XCTAssertThrowsError(try PeerRules.checkKey("short"))
        XCTAssertThrowsError(try PeerRules.checkKey(String(repeating: "A", count: 43) + "="), "All zeros")
        XCTAssertThrowsError(try PeerRules.checkKey(String(repeating: "!", count: 44)))
    }

    func testEndpointsThatCouldInjectOrStayOnThePhoneAreRefused() {
        for bad in ["", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "0.0.0.0", "::1", "evil\nPostUp=x", "host:1", "a b.example", "999.1.1.1", "1.2.3", "localhost"] {
            rejected(peer(host: bad))
        }
    }

    func testPortsAndAddressesAreBounded() {
        for port in [0, 70000] {
            rejected(Peer(serverPublicKey: keyA, endpointHost: "5.75.10.20", listenPort: port, addresses: ["10.66.0.9/32"], dns: ["1.1.1.1"], allowedIPs: ["0.0.0.0/0", "::/0"], keepalive: 25))
        }
        for addresses in [[], ["10.66.0.9/24"], ["8.8.8.8/32"], ["fd00::9/128"]] {
            rejected(Peer(serverPublicKey: keyA, endpointHost: "5.75.10.20", listenPort: 1, addresses: addresses, dns: ["1.1.1.1"], allowedIPs: ["0.0.0.0/0", "::/0"], keepalive: 25))
        }
    }

    func testIPv6EndpointsGetBrackets() throws {
        let p = try AdvancedSettings().profile(server: germany, peer: peer(host: "2001:db8::1"))
        XCTAssertEqual(p.endpoint, "[2001:db8::1]:51821")
        XCTAssertEqual(try AdvancedSettings().profile(server: germany, peer: peer()).endpoint, "5.75.10.20:51821")
    }
}

final class SettingsTests: XCTestCase {
    func testDNSAndMTUReachTheProfileButNeverTheRoutes() throws {
        var s = AdvancedSettings()
        s.customDNS = ["9.9.9.9"]
        s.mtu = 1380
        let p = try s.profile(server: germany, peer: peer())
        XCTAssertEqual(p.dns, ["9.9.9.9"])
        XCTAssertEqual(p.mtu, 1380)
        XCTAssertEqual(Set(p.allowedIPs), ["0.0.0.0/0", "::/0"])
    }

    func testAutomaticUsesTheServersResolver() throws {
        let p = try AdvancedSettings().profile(server: germany, peer: peer())
        XCTAssertEqual(p.dns, ["10.66.0.1"])
        XCTAssertNil(p.mtu)
    }

    func testBadValuesAreCaught() {
        var s = AdvancedSettings()
        XCTAssertNil(s.problem)
        s.mtu = 500
        XCTAssertNotNil(s.problem)
        s.mtu = 1420
        XCTAssertNil(s.problem)
        s.customDNS = ["dns.example.com"]
        XCTAssertNotNil(s.problem)
        s.customDNS = ["1.1.1.1", "2606:4700:4700::1111"]
        XCTAssertNil(s.problem)
    }

    func testTheProfileHoldsNoPrivateKeyAndSurvivesTheConfiguration() throws {
        let p = try AdvancedSettings().profile(server: germany, peer: peer())
        let text = try p.encoded()
        XCTAssertFalse(text.contains(keyB))
        XCTAssertFalse(text.lowercased().contains("private"))
        XCTAssertEqual(TunnelProfile.decoded(from: text), p)
        XCTAssertNil(TunnelProfile.decoded(from: "not json"))
    }
}

final class TunnelStatsTests: XCTestCase {
    func testReadsOnlyTheFiguresAndKeepsNoKey() throws {
        let uapi = "private_key=aabbcc\nlisten_port=1234\npublic_key=ddeeff\nendpoint=5.75.10.20:51821\nlast_handshake_time_sec=1700000000\nlast_handshake_time_nsec=500000000\nrx_bytes=100\ntx_bytes=200\nerrno=0\n"
        let s = TunnelStats.parse(uapi: uapi)
        XCTAssertEqual(s.rxBytes, 100)
        XCTAssertEqual(s.txBytes, 200)
        XCTAssertEqual(try XCTUnwrap(s.lastHandshake).timeIntervalSince1970, 1_700_000_000.5, accuracy: 0.001)
        let crossing = String(decoding: try JSONEncoder().encode(s), as: UTF8.self)
        XCTAssertFalse(crossing.contains("aabbcc"), "What crosses to the app must not hold the key")
        XCTAssertEqual(try JSONDecoder().decode(TunnelStats.self, from: Data(crossing.utf8)), s)
    }

    func testNoHandshakeYetIsNil() {
        XCTAssertNil(TunnelStats.parse(uapi: "last_handshake_time_sec=0\nlast_handshake_time_nsec=0\n").lastHandshake)
        XCTAssertNil(TunnelStats.parse(uapi: "").lastHandshake)
    }
}

final class KeyAndLogTests: XCTestCase {
    func testKeysMatchTheRFC7748Vector() throws {
        let alice = Data(hex: "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        let pair = CryptoKitKeyMaker.pair(raw: alice)
        XCTAssertEqual(Data(base64Encoded: pair.publicKey)?.hexText, "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
    }

    func testGeneratedKeysAreValidAndDifferent() throws {
        let a = CryptoKitKeyMaker().generate(), b = CryptoKitKeyMaker().generate()
        XCTAssertNotEqual(a.privateKey, b.privateKey)
        for key in [a.privateKey, a.publicKey] { try PeerRules.checkKey(key) }
        let raw = [UInt8](try XCTUnwrap(Data(base64Encoded: a.privateKey)))
        XCTAssertEqual(raw[0] & 7, 0, "Clamped")
        XCTAssertEqual(raw[31] & 0xC0, 0x40, "Clamped")
    }

    func testSecretsCantGetIntoTheLog() {
        for secret in [keyA, "kd.abcdef", "PrivateKey = \(keyB)", "5.75.10.20", "2001:db8::1", String(repeating: "x", count: 41)] {
            XCTAssertEqual(Diag.clean(secret), "[removed]", secret)
        }
        XCTAssertEqual(Diag.clean("trial"), "trial")
        XCTAssertEqual(Diag.line("connected", [("server", "2"), ("token", "kd.abc")]), "connected server=2 token=[removed]")
    }
}

extension Data {
    init(hex: String) {
        var out = [UInt8]()
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2)
            out.append(UInt8(hex[i..<j], radix: 16) ?? 0)
            i = j
        }
        self.init(out)
    }

    var hexText: String { map { String(format: "%02x", $0) }.joined() }
}
