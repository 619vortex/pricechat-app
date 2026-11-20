import XCTest
@testable import KiwiCore

final class AccountDeletionTests: XCTestCase {
    private let store = MemoryStore()
    private let api = FakeAPI()

    private func deletion() -> AccountDeletion {
        let api = self.api
        return AccountDeletion(api: api, store: store, stopVPN: { api.calls.append("stop vpn") }, removeVPN: { api.calls.append("remove vpn") })
    }

    override func setUp() {
        super.setUp()
        store.devices = [2: SavedDevice(deviceId: 51, peer: peer()), 1: SavedDevice(deviceId: 50, peer: peer())]
        store.keys = [2: keyB, 1: keyB]
    }

    func testItAllHappensInTheSafeOrderAndEverythingHereGoes() async throws {
        try await deletion().run(typed: "iosuser", username: "iosuser")
        XCTAssertEqual(api.calls, ["stop vpn", "delete 50", "delete 51", "delete-account iosuser", "remove vpn"])
        XCTAssertNil(store.token)
        XCTAssertTrue(store.devices.isEmpty)
        XCTAssertTrue(store.keys.isEmpty, "No WireGuard key may stay on the phone")
        XCTAssertTrue(store.wipedAll)
    }

    func testAWrongUsernameTouchesNothing() async {
        for typed in ["", "iosuse", "IOSUSER", "iosuser2"] {
            do {
                try await deletion().run(typed: typed, username: "iosuser")
                XCTFail("\(typed) must be refused")
            } catch {
                XCTAssertEqual(error as? AccountDeletionError, .mismatch)
            }
        }
        XCTAssertTrue(api.calls.isEmpty, "Not even the tunnel is stopped")
        XCTAssertEqual(store.token, "kd.token")
        XCTAssertFalse(store.wipedAll)
    }

    func testSpacesAroundTheUsernameAreForgivenAndTheRealUsernameIsSent() async throws {
        XCTAssertTrue(AccountDeletion.matches(typed: "  iosuser \n", username: "iosuser"))
        try await deletion().run(typed: " iosuser ", username: "iosuser")
        XCTAssertTrue(api.calls.contains("delete-account iosuser"))
    }

    func testNoUsernameYetMeansNoDeletion() async {
        XCTAssertFalse(AccountDeletion.matches(typed: "", username: nil))
        XCTAssertFalse(AccountDeletion.matches(typed: "", username: ""))
        do {
            try await deletion().run(typed: "x", username: nil)
            XCTFail("Must be refused")
        } catch {
            XCTAssertEqual(error as? AccountDeletionError, .usernameUnknown)
        }
        XCTAssertTrue(api.calls.isEmpty)
    }

    func testSignedOutCantDelete() async {
        store.token = nil
        do {
            try await deletion().run(typed: "iosuser", username: "iosuser")
            XCTFail("Must be refused")
        } catch {
            XCTAssertEqual(error as? AccountDeletionError, .notSignedIn)
        }
        XCTAssertTrue(api.calls.isEmpty)
    }

    func testIfTheServerRefusesThePersonStaysSignedInWithTheConfigurationKept() async {
        api.deleteAccountError = ApiError(status: 400, message: "Type your username exactly to confirm.")
        do {
            try await deletion().run(typed: "iosuser", username: "iosuser")
            XCTFail("Must report the refusal")
        } catch {
            XCTAssertEqual((error as? ApiError)?.message, "Type your username exactly to confirm.")
        }
        XCTAssertFalse(api.calls.contains("remove vpn"))
        XCTAssertEqual(store.token, "kd.token")
        XCTAssertFalse(store.wipedAll)
    }

    func testAnUnreachableServerDeletesNothingHere() async {
        api.deleteAccountError = ApiError(status: 0, message: unreachableMessage)
        do {
            try await deletion().run(typed: "iosuser", username: "iosuser")
            XCTFail("Must report it")
        } catch {
            XCTAssertTrue((error as? ApiError)?.unreachable == true)
        }
        XCTAssertEqual(store.token, "kd.token")
    }

    func testAnEndedSessionIsReportedAsSignedOut() async {
        api.deleteAccountError = ApiError(status: 401, message: "Sign in first.")
        do {
            try await deletion().run(typed: "iosuser", username: "iosuser")
            XCTFail("Must report it")
        } catch {
            XCTAssertTrue((error as? ApiError)?.signedOut == true)
        }
    }

    func testTheRequestIsTheBackendsOwnDeleteWithOnlyTheConfirmation() async throws {
        let t = FakeTransport()
        t.body = #"{"deleted":true}"#
        let real = try XCTUnwrap(HTTPKiwiAPI(base: "https://pricechat.test", transport: t))
        try await real.deleteAccount(token: "kd.secret", confirmUsername: "iosuser")
        let r = try XCTUnwrap(t.seen.first)
        XCTAssertEqual(r.httpMethod, "DELETE")
        XCTAssertEqual(r.url?.absoluteString, "https://pricechat.test/api/members/me")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Cookie"), "pc_session=kd.secret")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let sent = try JSONSerialization.jsonObject(with: try XCTUnwrap(r.httpBody)) as? [String: String]
        XCTAssertEqual(sent, ["confirm_username": "iosuser"])
    }

    func testTheServersRefusalReachesThePersonInItsOwnWords() async throws {
        let t = FakeTransport()
        t.status = 400
        t.body = #"{"detail":"Type your username exactly to confirm."}"#
        let real = try XCTUnwrap(HTTPKiwiAPI(base: "https://pricechat.test", transport: t))
        do {
            try await real.deleteAccount(token: "kd.secret", confirmUsername: "nope")
            XCTFail("Must throw")
        } catch let e as ApiError {
            XCTAssertEqual(e.status, 400)
            XCTAssertEqual(e.localizedDescription, "Type your username exactly to confirm.")
        }
    }
}
