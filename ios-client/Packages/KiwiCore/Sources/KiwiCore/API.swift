import Foundation

public let kiwiBaseURL = "https://pricechat.live"
public let unreachableMessage = "Couldn't reach Price Chat. Check your connection and try again."

/// status 0: the request never got an answer.
public struct ApiError: Error, LocalizedError, Equatable, Sendable {
    public let status: Int
    public let message: String
    public init(status: Int, message: String) { self.status = status; self.message = message }
    public var signedOut: Bool { status == 401 }
    public var unreachable: Bool { status == 0 }
    public var errorDescription: String? { message }
}

/// Everything the app asks Price Chat. Every call and field here is one the backend really has: docs/API-CONTRACT.md.
public protocol KiwiAPI: Sendable {
    func account(token: String) async throws -> Account
    func startTrial(token: String) async throws -> Account
    func peer(token: String, deviceId: Int64) async throws -> Peer
    func addDevice(token: String, name: String, serverId: Int64, publicKey: String) async throws -> NewDevice
    func deleteDevice(token: String, deviceId: Int64) async throws
    func me(token: String) async throws -> Me
    /// Passport and capabilities. Answers 404 until the shared API is deployed, which the app treats as "not available yet".
    func unifiedState(token: String, version: String) async throws -> UnifiedState
    func exchangeCode(code: String, verifier: String) async throws -> String
    func refresh(token: String) async throws -> String
    func logout(token: String) async throws
    func myIP() async throws -> String
}

/// Sends one request. URLSessionTransport is the real one, and tests stand in for it.
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, Int)
}

/// Never logs a request, a header or a body. Refuses redirects, so the session can't be sent anywhere but Price Chat, and keeps no cookies or cache of its own.
public final class URLSessionTransport: NSObject, HTTPTransport, URLSessionTaskDelegate, @unchecked Sendable {
    private lazy var session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.httpCookieStorage = nil
        c.httpShouldSetCookies = false
        c.urlCache = nil
        c.timeoutIntervalForRequest = 30
        c.timeoutIntervalForResource = 35
        c.waitsForConnectivity = false
        return URLSession(configuration: c, delegate: self, delegateQueue: nil)
    }()

    public override init() { super.init() }

    public func send(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await session.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

private struct TokenReply: Decodable { let token: String }
private struct IPReply: Decodable { let ip: String }
private struct DetailReply: Decodable { let detail: String }
private struct TokenInput: Encodable { let code: String; let verifier: String }
private struct DeviceInput: Encodable { let name: String; let server_id: Int64; let public_key: String }

public struct HTTPKiwiAPI: KiwiAPI {
    private let base: String
    private let transport: HTTPTransport
    private let userAgent: String

    /// nil if it isn't TLS. Price Chat is only ever reached over TLS.
    public init?(base: String = kiwiBaseURL, transport: HTTPTransport = URLSessionTransport(), userAgent: String = "KiwiVPN-iOS") {
        guard base.hasPrefix("https://") else { return nil }
        self.base = base.hasSuffix("/") ? String(base.dropLast()) : base
        self.transport = transport
        self.userAgent = userAgent
    }

    private func request(_ path: String, token: String?, method: String = "GET", query: [URLQueryItem] = [], body: Data? = nil) throws -> URLRequest {
        guard var parts = URLComponents(string: "\(base)/api\(path)") else { throw ApiError(status: 0, message: unreadable) }
        if !query.isEmpty { parts.queryItems = query }
        guard let url = parts.url else { throw ApiError(status: 0, message: unreadable) }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.httpShouldHandleCookies = false // or URLSession would drop the Cookie header set below
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        r.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        if let token { r.setValue("pc_session=\(token)", forHTTPHeaderField: "Cookie") }
        if let body {
            r.httpBody = body
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return r
    }

    private var unreadable: String { "Price Chat gave an answer the app can't read." }

    private func send(_ r: URLRequest) async throws -> Data {
        let data: Data
        let status: Int
        do {
            (data, status) = try await transport.send(r)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw ApiError(status: 0, message: unreachableMessage)
        }
        guard (200..<300).contains(status) else {
            let detail = (try? JSONDecoder().decode(DetailReply.self, from: data))?.detail
            throw ApiError(status: status, message: detail ?? "Price Chat answered \(status).")
        }
        return data
    }

    private func parse<T: Decodable>(_ type: T.Type, _ r: URLRequest) async throws -> T {
        let data = try await send(r)
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw ApiError(status: 0, message: unreadable)
        }
    }

    public func account(token: String) async throws -> Account {
        try await parse(Account.self, request("/members/vpn", token: token))
    }

    public func startTrial(token: String) async throws -> Account {
        try await parse(Account.self, request("/members/vpn/trial", token: token, method: "POST"))
    }

    public func peer(token: String, deviceId: Int64) async throws -> Peer {
        try await parse(Peer.self, request("/members/vpn/devices/\(deviceId)/peer", token: token))
    }

    public func addDevice(token: String, name: String, serverId: Int64, publicKey: String) async throws -> NewDevice {
        let body = try JSONEncoder().encode(DeviceInput(name: name, server_id: serverId, public_key: publicKey))
        return try await parse(NewDevice.self, request("/members/vpn/devices", token: token, method: "POST", body: body))
    }

    public func deleteDevice(token: String, deviceId: Int64) async throws {
        _ = try await send(request("/members/vpn/devices/\(deviceId)", token: token, method: "DELETE"))
    }

    public func me(token: String) async throws -> Me {
        try await parse(Me.self, request("/members/me", token: token))
    }

    public func unifiedState(token: String, version: String) async throws -> UnifiedState {
        try await parse(UnifiedState.self, request("/members/vpn/v1/state", token: token, query: [URLQueryItem(name: "platform", value: "ios"), URLQueryItem(name: "version", value: version)]))
    }

    public func exchangeCode(code: String, verifier: String) async throws -> String {
        let body = try JSONEncoder().encode(TokenInput(code: code, verifier: verifier))
        return try await parse(TokenReply.self, request("/members/desktop/token", token: nil, method: "POST", body: body)).token
    }

    public func refresh(token: String) async throws -> String {
        try await parse(TokenReply.self, request("/members/desktop/refresh", token: token, method: "POST")).token
    }

    public func logout(token: String) async throws {
        _ = try await send(request("/members/logout", token: token, method: "POST"))
    }

    public func myIP() async throws -> String {
        try await parse(IPReply.self, request("/members/vpn/ip", token: nil)).ip
    }
}
