import Combine
import Foundation

public enum TunnelStatus: Sendable, Equatable { case invalid, disconnected, connecting, connected, reasserting, disconnecting }

/// What the connection code needs from iOS. NETunnelDriver is the real one. A thrown error's description is fit to show.
public protocol TunnelDriver: AnyObject, Sendable {
    /// Saves the VPN configuration. The first time, iOS asks the person to allow it.
    func install(profile: TunnelProfile, settings: AdvancedSettings) async throws
    func start() async throws
    func stop() async
    func status() async -> TunnelStatus
    func stats() async -> TunnelStats?
    /// When iOS says the current connection came up, which survives the app being closed.
    func connectedDate() async -> Date?
    /// The configuration iOS holds now, if any: what a restarted app reads to find out what's running.
    func installedProfile() async -> TunnelProfile?
    func setOnDemand(_ on: Bool, settings: AdvancedSettings) async throws
}

public struct TunnelLink: Equatable, Sendable {
    public var since: Date
    public var endpoint: String
    public var dns: [String]
    public var lastHandshake: Date?
    public var rxBytes: UInt64
    public var txBytes: UInt64
    public var publicIP: String?
    public var ipMatchesServer: Bool
    public init(since: Date, endpoint: String, dns: [String], lastHandshake: Date? = nil, rxBytes: UInt64 = 0, txBytes: UInt64 = 0, publicIP: String? = nil, ipMatchesServer: Bool = false) {
        self.since = since; self.endpoint = endpoint; self.dns = dns; self.lastHandshake = lastHandshake; self.rxBytes = rxBytes; self.txBytes = txBytes; self.publicIP = publicIP; self.ipMatchesServer = ipMatchesServer
    }
}

/// The states the rest of the app sees. `connected` is only ever reached with iOS reporting connected AND a real WireGuard handshake.
public enum ConnectionState: Equatable, Sendable {
    public enum Step: Sendable { case preparing, starting, waitingForServer }
    case disconnected(notice: String?)
    case connecting(target: ServerRef, step: Step)
    case connected(server: ServerRef, link: TunnelLink, notice: String?)
    /// The tunnel is up and the server has stopped answering, as when the network drops. WireGuard keeps trying.
    case reconnecting(server: ServerRef, link: TunnelLink)
    case disconnecting(server: ServerRef?)
    case failed(message: String, target: ServerRef?)

    public var isActive: Bool {
        switch self {
        case .connecting, .connected, .reconnecting: return true
        default: return false
        }
    }

    public var word: String {
        switch self {
        case .connected: return "Protected"
        case .reconnecting: return "Reconnecting"
        case .connecting: return "Connecting"
        case .disconnecting: return "Disconnecting"
        case .disconnected: return "Unprotected"
        case .failed: return "Connection failed"
        }
    }

    public var server: ServerRef? {
        switch self {
        case .connected(let s, _, _), .reconnecting(let s, _): return s
        case .connecting(let t, _): return t
        case .disconnecting(let s): return s
        case .failed(_, let t): return t
        case .disconnected: return nil
        }
    }

    public var link: TunnelLink? {
        switch self {
        case .connected(_, let l, _), .reconnecting(_, let l): return l
        default: return nil
        }
    }
}

public struct Timings: Sendable {
    public var connectLimit: TimeInterval = 45
    public var stopLimit: TimeInterval = 15
    public var poll: TimeInterval = 0.5
    /// WireGuard rekeys every 2 minutes with keepalives on, and refuses a session after 3. Older than this, the server has stopped answering.
    public var stale: TimeInterval = 195
    public init() {}
}

/// The one place the tunnel is started or stopped: disconnected -> connecting -> connected -> disconnecting -> disconnected, with reconnecting and failed.
/// Each new command takes over from the last by raising `generation`, and old work stops at its next check. Nothing waits without a limit, and a tunnel that dies reads as failed rather than as an idle button.
/// The same rules as Android, adapted to iOS, where the tunnel runs in its own process and outlives the app.
@MainActor
public final class ConnectionCoordinator: ObservableObject {
    @Published public private(set) var state: ConnectionState = .disconnected(notice: nil)

    private let driver: TunnelDriver
    private let source: ProfileSource
    private let ip: @Sendable () async throws -> String
    private let now: @Sendable () -> Date
    private let sleep: @Sendable (TimeInterval) async -> Void
    private let timings: Timings
    private let settings: @Sendable () -> AdvancedSettings
    private var generation = 0
    /// Set while connect or disconnect is doing the work, so a status check doesn't talk over it.
    private var working = 0
    private var lastLink: TunnelLink?

    public init(driver: TunnelDriver, source: ProfileSource, ip: @escaping @Sendable () async throws -> String, settings: @escaping @Sendable () -> AdvancedSettings,
                now: @escaping @Sendable () -> Date = { Date() }, sleep: @escaping @Sendable (TimeInterval) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }, timings: Timings = Timings()) {
        self.driver = driver; self.source = source; self.ip = ip; self.settings = settings; self.now = now; self.sleep = sleep; self.timings = timings
    }

    private var runningServer: ServerRef? {
        switch state {
        case .connected(let s, _, _), .reconnecting(let s, _): return s
        default: return nil
        }
    }

    /// Connect, or switch if a tunnel is already running. A repeat of what's already happening does nothing. Returns when it has connected or failed.
    public func connect(_ server: ServerRef) async {
        switch state {
        case .connecting(let t, _) where t.id == server.id: return
        case .connected(let s, _, _) where s.id == server.id: return
        case .reconnecting(let s, _) where s.id == server.id: return
        default: break
        }
        await start(server)
    }

    /// New DNS, MTU or routing settings can only take effect on a new interface, so this rebuilds it.
    public func reapply() async {
        guard let server = runningServer else { return }
        await start(server)
    }

    private func start(_ server: ServerRef) async {
        working += 1
        defer { working -= 1 }
        generation += 1
        let mine = generation
        var running = runningServer
        state = .connecting(target: server, step: .preparing)

        let wanted = settings()
        let profile: TunnelProfile
        do {
            // Fetched before the old tunnel goes. With on-demand rules nothing else may get through, and a failure here leaves the person on the working connection rather than off it.
            do {
                profile = try await source.profile(serverId: server.id, server: server, keepServer: running?.id, settings: wanted).profile
            } catch ProfileError.deviceLimit(tooMany: false) where running != nil {
                // The account is full and the only device to give up is the one in use. The tunnel has to go first, because removing its device would cut off this very request.
                await stopAndWait()
                guard mine == generation else { return }
                running = nil
                profile = try await source.profile(serverId: server.id, server: server, keepServer: nil, settings: wanted).profile
            }
        } catch {
            guard mine == generation else { return }
            await giveUp(error.localizedDescription, server: server, running: running)
            return
        }
        guard mine == generation else { return }

        state = .connecting(target: server, step: .starting)
        if running != nil {
            await stopAndWait()
            guard mine == generation else { return }
        }
        do {
            try await driver.install(profile: profile, settings: wanted)
            guard mine == generation else { return }
            try await driver.start()
        } catch {
            guard mine == generation else { return }
            await driver.stop()
            state = .failed(message: error.localizedDescription, target: server)
            return
        }
        guard mine == generation else { return }
        state = .connecting(target: server, step: .waitingForServer)

        let deadline = now().addingTimeInterval(timings.connectLimit)
        var first: TunnelStats?
        while now() < deadline {
            let status = await driver.status()
            guard mine == generation else { return }
            if status == .disconnected || status == .invalid, now().timeIntervalSince(deadline) > -(timings.connectLimit - 2) {
                state = .failed(message: "iOS stopped the connection before it finished. Please try again.", target: server)
                return
            }
            if status == .connected, let stats = await driver.stats(), stats.lastHandshake != nil {
                first = stats
                break
            }
            guard mine == generation else { return }
            await sleep(timings.poll)
            guard mine == generation else { return }
        }
        guard let stats = first else {
            await driver.stop()
            guard mine == generation else { return }
            state = .failed(message: "The server didn't answer in time. Check your connection, or try another country.", target: server)
            return
        }
        let since = await driver.connectedDate() ?? now()
        guard mine == generation else { return }
        adopt(server: server, profile: profile, stats: stats, since: since, notice: nil)
        await checkIP(mine: mine, server: server)
    }

    private func adopt(server: ServerRef, profile: TunnelProfile, stats: TunnelStats, since: Date, notice: String?) {
        let link = TunnelLink(since: since, endpoint: profile.endpointHost, dns: profile.dns, lastHandshake: stats.lastHandshake, rxBytes: stats.rxBytes, txBytes: stats.txBytes)
        lastLink = link
        state = .connected(server: server, link: link, notice: notice)
    }

    /// The address Price Chat sees, asked through the tunnel. Shown as verified only when it's the server's own.
    private func checkIP(mine: Int, server: ServerRef) async {
        for _ in 0..<3 {
            if let address = try? await ip() {
                guard mine == generation, case .connected(let s, var link, let notice) = state, s.id == server.id else { return }
                link.publicIP = address
                link.ipMatchesServer = address == link.endpoint
                lastLink = link
                state = .connected(server: s, link: link, notice: notice)
                return
            }
            await sleep(2)
            guard mine == generation else { return }
        }
    }

    /// Stays on the old tunnel if there is one, since a failed switch shouldn't cost the working connection.
    private func giveUp(_ message: String, server: ServerRef, running: ServerRef?) async {
        if let running, await driver.status() == .connected, let link = lastLink {
            state = .connected(server: running, link: link, notice: "Couldn't switch: \(message)")
        } else {
            state = .failed(message: message, target: server)
        }
    }

    public func disconnect() async {
        if case .disconnecting = state { return }
        working += 1
        defer { working -= 1 }
        generation += 1
        let mine = generation
        state = .disconnecting(server: state.server)
        // Otherwise an on-demand rule would bring the tunnel straight back. The person is told this in Settings.
        try? await driver.setOnDemand(false, settings: settings())
        let stopped = await stopAndWait()
        guard mine == generation else { return }
        state = stopped ? .disconnected(notice: nil) : .failed(message: "iOS didn't stop the tunnel. Try again, or turn the VPN off in Settings.", target: nil)
    }

    @discardableResult
    private func stopAndWait() async -> Bool {
        await driver.stop()
        let deadline = now().addingTimeInterval(timings.stopLimit)
        while now() < deadline {
            let status = await driver.status()
            if status == .disconnected || status == .invalid { return true }
            await sleep(timings.poll)
        }
        return false
    }

    /// At startup, when the app comes back, and every few seconds while it's on screen: what iOS says is running decides what's shown. The tunnel runs in its own process, so it may have outlived the app, or been started by an on-demand rule.
    public func reconcile() async {
        guard working == 0 else { return }
        let status = await driver.status()
        guard working == 0 else { return }
        guard status == .connected || status == .reasserting || status == .connecting, let profile = await driver.installedProfile() else {
            if state.isActive {
                state = .failed(message: "iOS stopped the connection, or another VPN started. Traffic is no longer going through Kiwi VPN.", target: state.server)
            }
            return
        }
        let server = ServerRef(id: profile.serverId, name: profile.serverName, countryCode: profile.countryCode)
        await tick(profile: profile, server: server)
    }

    private func tick(profile: TunnelProfile, server: ServerRef) async {
        let mine = generation
        guard let stats = await driver.stats(), mine == generation, working == 0 else { return }
        guard let handshake = stats.lastHandshake else {
            // Up, with no handshake yet: never Protected.
            if case .connected = state { return }
            state = .connecting(target: server, step: .waitingForServer)
            return
        }
        let since = await driver.connectedDate() ?? lastLink?.since ?? now()
        guard mine == generation else { return }
        var link = lastLink ?? TunnelLink(since: since, endpoint: profile.endpointHost, dns: profile.dns)
        link.since = since
        link.endpoint = profile.endpointHost
        link.dns = profile.dns
        link.lastHandshake = handshake
        link.rxBytes = stats.rxBytes
        link.txBytes = stats.txBytes
        if now().timeIntervalSince(handshake) > timings.stale {
            link.publicIP = nil
            link.ipMatchesServer = false
            lastLink = link
            state = .reconnecting(server: server, link: link)
            return
        }
        var notice: String?
        var recovered = true
        if case .connected(_, _, let n) = state {
            notice = n
            recovered = false
        }
        lastLink = link
        state = .connected(server: server, link: link, notice: notice)
        if recovered || link.publicIP == nil { await checkIP(mine: mine, server: server) }
    }
}
