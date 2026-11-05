import CryptoKit
import Foundation
import Security

public let signInCallbackScheme = "kiwivpn"

/// Signing in through ASWebAuthenticationSession, so the app never sees a password. PKCE (RFC 7636): a random verifier stays here, and the page gets only its hash.
/// The code that comes back is useless without the verifier, and works once. The token is swapped for it in the app and never appears in a URL. See docs/SIGN-IN.md.
public struct SignInAttempt: Sendable {
    public let verifier: String
    public let challenge: String
    public let state: String

    public static func randomText(bytes: Int) -> String? {
        var raw = [UInt8](repeating: 0, count: bytes)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes, &raw) == errSecSuccess else { return nil }
        return base64URL(Data(raw))
    }

    public static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }

    public static func challenge(of verifier: String) -> String {
        base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
    }

    public static func create() -> SignInAttempt? {
        guard let verifier = randomText(bytes: 32), let state = randomText(bytes: 24) else { return nil }
        return SignInAttempt(verifier: verifier, challenge: challenge(of: verifier), state: state)
    }

    /// `port` is always sent, because the live page requires one. `client=ios` makes a page with the proposed change use the callback scheme instead: a page without it ignores the word and uses the port.
    public func pageURL(base: String, port: UInt16) -> URL? {
        URL(string: "\(base)/vpn/desktop?challenge=\(challenge)&port=\(port)&state=\(state)&client=ios")
    }
}

private func decoded(_ value: String) -> String? {
    value.replacingOccurrences(of: "+", with: " ").removingPercentEncoding
}

private func queryValues(_ query: Substring) -> [String: String] {
    var out: [String: String] = [:]
    for pair in query.split(separator: "&") {
        guard let at = pair.firstIndex(of: "="), at != pair.startIndex else { continue }
        if let value = decoded(String(pair[pair.index(after: at)...])) { out[String(pair[..<at])] = value }
    }
    return out
}

private func checkedCode(_ values: [String: String], state: String) -> String? {
    guard values["state"] == state, let code = values["code"], (20...100).contains(code.count) else { return nil }
    return code
}

/// The code from a request line on the loopback listener, only if it's our callback, carrying our state.
public func readLoopbackCallback(requestLine: String, state: String) -> String? {
    let parts = requestLine.split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\r" || $0 == "\n" })
    guard parts.count >= 2, parts[0] == "GET", parts[1].hasPrefix("/callback?") else { return nil }
    return checkedCode(queryValues(parts[1].dropFirst("/callback?".count)), state: state)
}

/// The code from the URL ASWebAuthenticationSession hands back: exactly kiwivpn://callback, carrying our state. Nothing else counts.
public func readSchemeCallback(url: URL, state: String) -> String? {
    guard url.scheme == signInCallbackScheme, url.host == "callback", url.path.isEmpty || url.path == "/", let query = url.query else { return nil }
    return checkedCode(queryValues(Substring(query)), state: state)
}

/// What the loopback listener answers. On success it sends the browser to the scheme, which ends the sign-in sheet. The code isn't in that URL or the page.
public func loopbackReply(ok: Bool) -> Data {
    let back = "\(signInCallbackScheme)://signed-in"
    let head = ok
        ? "HTTP/1.1 302 Found\r\nLocation: \(back)\r\nContent-Length: 0\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        : "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\nContent-Length: 22\r\nCache-Control: no-store\r\nConnection: close\r\n\r\nThis link isn't valid."
    return Data(head.utf8)
}
