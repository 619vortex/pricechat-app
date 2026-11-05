import Foundation

// The backend's answers, exactly as docs/API-CONTRACT.md records them from backend/app/vpn/service.py and unified.py.
// Unknown fields are ignored, so a new field on the server can't break the app. backend/tests/test_ios_contract.py holds the Fixtures against the real backend.

public struct Server: Codable, Equatable, Identifiable, Sendable {
    public let id: Int64
    public let name: String
    public let region: String
    public let countryCode: String
    public let online: Bool
    public let recommended: Bool

    enum CodingKeys: String, CodingKey { case id, name, region, online, recommended, countryCode = "country_code" }

    public init(id: Int64, name: String, region: String = "", countryCode: String = "", online: Bool, recommended: Bool = false) {
        self.id = id; self.name = name; self.region = region; self.countryCode = countryCode; self.online = online; self.recommended = recommended
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        region = try c.decodeIfPresent(String.self, forKey: .region) ?? ""
        countryCode = try c.decodeIfPresent(String.self, forKey: .countryCode) ?? ""
        online = try c.decode(Bool.self, forKey: .online)
        recommended = try c.decodeIfPresent(Bool.self, forKey: .recommended) ?? false
    }

    public var ref: ServerRef { ServerRef(id: id, name: name, countryCode: countryCode) }
}

/// A server as the connection code needs it, and as it's remembered between runs.
public struct ServerRef: Codable, Equatable, Hashable, Sendable {
    public let id: Int64
    public let name: String
    public let countryCode: String
    public init(id: Int64, name: String, countryCode: String = "") { self.id = id; self.name = name; self.countryCode = countryCode }
}

public struct Device: Codable, Equatable, Identifiable, Sendable {
    public let id: Int64
    public let name: String
    public let serverId: Int64
    enum CodingKeys: String, CodingKey { case id, name, serverId = "server_id" }
    public init(id: Int64, name: String, serverId: Int64) { self.id = id; self.name = name; self.serverId = serverId }
}

public struct Plan: Codable, Equatable, Sendable {
    public let kind: String
    public let until: String?
    public let daysLeft: Int64?
    enum CodingKeys: String, CodingKey { case kind, until, daysLeft = "days_left" }
    public init(kind: String, until: String? = nil, daysLeft: Int64? = nil) { self.kind = kind; self.until = until; self.daysLeft = daysLeft }
}

public struct Trial: Codable, Equatable, Sendable {
    public let available: Bool
    public let days: Int64
    public init(available: Bool, days: Int64) { self.available = available; self.days = days }
}

public struct Account: Codable, Equatable, Sendable {
    public let eligible: Bool
    public let reason: String?
    public let payments: Bool
    public let maxDevices: Int64
    public let servers: [Server]
    public var devices: [Device]
    public let plan: Plan?
    public let trial: Trial?

    enum CodingKeys: String, CodingKey { case eligible, reason, payments, servers, devices, plan, trial, maxDevices = "max_devices" }

    public init(eligible: Bool, reason: String? = nil, payments: Bool = false, maxDevices: Int64 = 3, servers: [Server] = [], devices: [Device] = [], plan: Plan? = nil, trial: Trial? = nil) {
        self.eligible = eligible; self.reason = reason; self.payments = payments; self.maxDevices = maxDevices
        self.servers = servers; self.devices = devices; self.plan = plan; self.trial = trial
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        eligible = try c.decode(Bool.self, forKey: .eligible)
        reason = try c.decodeIfPresent(String.self, forKey: .reason)
        payments = try c.decodeIfPresent(Bool.self, forKey: .payments) ?? false
        maxDevices = try c.decodeIfPresent(Int64.self, forKey: .maxDevices) ?? 0
        servers = try c.decodeIfPresent([Server].self, forKey: .servers) ?? []
        devices = try c.decodeIfPresent([Device].self, forKey: .devices) ?? []
        plan = try c.decodeIfPresent(Plan.self, forKey: .plan)
        trial = try c.decodeIfPresent(Trial.self, forKey: .trial)
    }
}

public struct Peer: Codable, Equatable, Sendable {
    public let serverPublicKey: String
    public let endpointHost: String
    public let listenPort: Int
    public let addresses: [String]
    public let dns: [String]
    public let allowedIPs: [String]
    public let keepalive: Int

    enum CodingKeys: String, CodingKey {
        case addresses, dns, keepalive
        case serverPublicKey = "server_public_key", endpointHost = "endpoint_host", listenPort = "listen_port", allowedIPs = "allowed_ips"
    }

    public init(serverPublicKey: String, endpointHost: String, listenPort: Int, addresses: [String], dns: [String], allowedIPs: [String], keepalive: Int) {
        self.serverPublicKey = serverPublicKey; self.endpointHost = endpointHost; self.listenPort = listenPort
        self.addresses = addresses; self.dns = dns; self.allowedIPs = allowedIPs; self.keepalive = keepalive
    }
}

public struct NewDevice: Codable, Equatable, Sendable {
    public let device: Device
    public let peer: Peer
    public init(device: Device, peer: Peer) { self.device = device; self.peer = peer }
}

// MARK: - Kiwi Passport and capabilities: GET /members/vpn/v1/state. Answers 404 until the shared API is deployed, so all of it is optional to the app.

public struct RankInfo: Codable, Equatable, Sendable {
    public let level: Int
    public let key: String
    public let name: String
    public init(level: Int, key: String, name: String) { self.level = level; self.key = key; self.name = name }
}

public struct RankProgress: Codable, Equatable, Sendable {
    public let current: RankInfo
    public let next: RankInfo?
    public let score: Int64
    public let xpToNext: Int64
    public let progress: Double
    enum CodingKeys: String, CodingKey { case current, next, score, progress, xpToNext = "xp_to_next" }
}

public struct Badge: Codable, Equatable, Identifiable, Sendable {
    public let key: String
    public let name: String
    public let description: String
    public var id: String { key }
}

public struct Standing: Codable, Equatable, Sendable {
    public let position: Int64
}

public struct ReferralInfo: Codable, Equatable, Sendable {
    public let code: String
    public let link: String
    public let joined: Int64
    public let qualified: Int64
}

public struct Passport: Codable, Equatable, Sendable {
    public let username: String
    public let displayName: String
    public let xp: Int64
    public let rank: RankProgress
    public let badges: [Badge]
    public let standing: Standing?
    public let referral: ReferralInfo?
    enum CodingKeys: String, CodingKey { case username, xp, rank, badges, standing, referral, displayName = "display_name" }
}

public struct Mission: Codable, Equatable, Identifiable, Sendable {
    public let key: String
    public let title: String
    public let xp: Int64
    public var id: String { key }
}

public struct UnifiedState: Codable, Equatable, Sendable {
    public let capabilities: [String: Bool]
    public let passport: Passport?
    public let missions: [Mission]
    enum CodingKeys: String, CodingKey { case capabilities, passport, missions }

    public init(capabilities: [String: Bool] = [:], passport: Passport? = nil, missions: [Mission] = []) {
        self.capabilities = capabilities; self.passport = passport; self.missions = missions
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        capabilities = try c.decodeIfPresent([String: Bool].self, forKey: .capabilities) ?? [:]
        passport = try c.decodeIfPresent(Passport.self, forKey: .passport)
        missions = try c.decodeIfPresent([Mission].self, forKey: .missions) ?? []
    }
}

public struct MemberInfo: Codable, Equatable, Sendable {
    public let username: String?
    public let displayName: String?
    enum CodingKeys: String, CodingKey { case username, displayName = "display_name" }
    public init(username: String?, displayName: String?) { self.username = username; self.displayName = displayName }
}

public struct Me: Codable, Equatable, Sendable {
    public let member: MemberInfo?
    public init(member: MemberInfo?) { self.member = member }
}

// MARK: - Account status: the server decides everything. This only reads its answer, and never starts or assumes a trial.

public enum StatusKind: Sendable { case trial, paid, unlimited, tester, needsTrial, expired, closed, blocked, unavailable }

public struct AccountStatus: Equatable, Sendable {
    public let kind: StatusKind
    public let label: String
    public let detail: String?
    public let canConnect: Bool
    public let canStartTrial: Bool

    public static func of(_ account: Account?) -> AccountStatus {
        guard let account else {
            return AccountStatus(kind: .unavailable, label: "Account unavailable", detail: "Couldn't reach Price Chat. Check your connection.", canConnect: false, canStartTrial: false)
        }
        let offer = account.trial?.available == true
        let left = dayText(account.plan?.daysLeft)
        switch account.plan?.kind {
        case "trial": return AccountStatus(kind: .trial, label: "Free trial: \(left)", detail: nil, canConnect: account.eligible, canStartTrial: false)
        case "paid": return AccountStatus(kind: .paid, label: "Subscription: \(left)", detail: nil, canConnect: account.eligible, canStartTrial: false)
        case "tester": return AccountStatus(kind: .tester, label: "Tester access: \(left)", detail: nil, canConnect: account.eligible, canStartTrial: false)
        case "owner", "royal", "free", "granted": return AccountStatus(kind: .unlimited, label: "Active", detail: nil, canConnect: account.eligible, canStartTrial: false)
        case "not_activated":
            if offer { return AccountStatus(kind: .needsTrial, label: "Free \(account.trial?.days ?? 30)-day trial available", detail: "One per account.", canConnect: false, canStartTrial: true) }
            return AccountStatus(kind: .expired, label: "No active plan", detail: account.reason, canConnect: false, canStartTrial: false)
        case "expired": return AccountStatus(kind: .expired, label: "Plan ended", detail: account.reason, canConnect: false, canStartTrial: false)
        case "closed": return AccountStatus(kind: .closed, label: "Kiwi VPN is unavailable", detail: account.reason, canConnect: false, canStartTrial: false)
        case "blocked": return AccountStatus(kind: .blocked, label: "This account can't use Kiwi VPN", detail: account.reason, canConnect: false, canStartTrial: false)
        default:
            // A kind this version doesn't know: follow the server's own yes or no, and say what it said.
            return AccountStatus(kind: account.eligible ? .unlimited : .unavailable, label: account.eligible ? "Active" : "Not available", detail: account.reason, canConnect: account.eligible, canStartTrial: offer)
        }
    }

    private static func dayText(_ n: Int64?) -> String {
        guard let n else { return "time left unknown" }
        switch n {
        case 0: return "less than a day left"
        case 1: return "1 day left"
        default: return "\(n) days left"
        }
    }
}

/// The last server used if it's still online, else the backend's recommendation, else any online one. The same rule as Windows and Android.
public func suggestedServer(_ servers: [Server], last: Int64?) -> Server? {
    servers.first { $0.id == last && $0.online } ?? servers.first { $0.online && $0.recommended } ?? servers.first { $0.online }
}
