import AuthenticationServices
import Foundation
import KiwiCore
import Network
import UIKit

enum SignInError: LocalizedError {
    case cancelled
    case failed

    var errorDescription: String? {
        switch self {
        case .cancelled: return "Signing in was cancelled."
        case .failed: return "Couldn't finish signing in. Please try again."
        }
    }
}

/// Hears the page's callback on 127.0.0.1, while the sign-in sheet is open. This is what today's live page uses: it can only send a code to loopback. See docs/SIGN-IN.md, and it needs testing on a real iPhone.
private final class LoopbackListener: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "live.pricechat.kiwivpn.signin")
    private let state: String
    private let lock = NSLock()
    private var code: String?

    init(state: String) throws {
        self.state = state
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: .ipv4(.loopback), port: .any)
        listener = try NWListener(using: parameters)
    }

    /// Returns the port once it's listening, or nil if it can't.
    func start() async -> UInt16? {
        await withCheckedContinuation { (done: CheckedContinuation<UInt16?, Never>) in
            let once = Once()
            listener.stateUpdateHandler = { [listener] update in
                switch update {
                case .ready: once.run { done.resume(returning: listener.port?.rawValue) }
                case .failed, .cancelled: once.run { done.resume(returning: nil) }
                default: break
                }
            }
            listener.newConnectionHandler = { [weak self] connection in self?.handle(connection) }
            listener.start(queue: queue)
        }
    }

    private func handle(_ connection: NWConnection) {
        connection.start(queue: queue)
        connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self] data, _, _, _ in
            guard let self else { return }
            let line = data.flatMap { String(data: $0, encoding: .isoLatin1) }?.components(separatedBy: "\r\n").first ?? ""
            let got = readLoopbackCallback(requestLine: line, state: self.state)
            if let got {
                self.lock.lock()
                self.code = got
                self.lock.unlock()
            }
            connection.send(content: loopbackReply(ok: got != nil), completion: .contentProcessed { _ in connection.cancel() })
        }
    }

    var received: String? {
        lock.lock(); defer { lock.unlock() }
        return code
    }

    func stop() { listener.cancel() }
}

private final class Once: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    func run(_ work: () -> Void) {
        lock.lock(); defer { lock.unlock() }
        if !done {
            done = true
            work()
        }
    }
}

/// ASWebAuthenticationSession: Apple's secure browser sheet. The app can't see what's typed there, and only this session receives the callback, which is what makes a custom scheme safe to use here.
/// The code arrives either by loopback, as today's live page does, or in the kiwivpn://callback URL, as the page will once the proposed change is deployed. Each is accepted only with this attempt's `state`.
@MainActor
final class SignInService: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        return scene?.windows.first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }

    /// The code and the verifier to swap it with.
    func signIn() async throws -> (code: String, verifier: String) {
        guard let attempt = SignInAttempt.create() else { throw SignInError.failed }
        let listener: LoopbackListener
        do {
            listener = try LoopbackListener(state: attempt.state)
        } catch {
            throw SignInError.failed
        }
        defer { listener.stop() }
        guard let port = await listener.start(), let url = attempt.pageURL(base: kiwiBaseURL, port: port) else { throw SignInError.failed }

        let returned: URL? = try await withCheckedThrowingContinuation { (done: CheckedContinuation<URL?, Error>) in
            let web = ASWebAuthenticationSession(url: url, callbackURLScheme: signInCallbackScheme) { callback, error in
                if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                    done.resume(throwing: SignInError.cancelled)
                } else if let error {
                    _ = error
                    done.resume(throwing: SignInError.failed)
                } else {
                    done.resume(returning: callback)
                }
            }
            web.presentationContextProvider = self
            web.prefersEphemeralWebBrowserSession = false // so a person already signed in to Price Chat in Safari needn't sign in again
            session = web
            if !web.start() { done.resume(throwing: SignInError.failed) }
        }
        session = nil
        if let returned, let code = readSchemeCallback(url: returned, state: attempt.state) { return (code, attempt.verifier) }
        if let code = listener.received { return (code, attempt.verifier) }
        throw SignInError.failed
    }
}
