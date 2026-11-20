import Foundation

public struct BadPeer: Error, Equatable {
    public let field: String
}

/// Checks every field the server sends before it can reach the tunnel, and rejects anything unexpected. A wrong answer, whatever the reason, must not quietly send traffic outside the tunnel:
/// so the routes must be exactly every IPv4 and every IPv6 address, and there must be a DNS server. The same rules as Android.
public enum PeerRules {
    private static let fullTunnel: Set<String> = ["0.0.0.0/0", "::/0"]

    public static func check(_ peer: Peer) throws {
        try checkKey(peer.serverPublicKey)
        try checkEndpoint(peer.endpointHost)
        guard (1...65535).contains(peer.listenPort) else { throw BadPeer(field: "port") }
        try checkAddresses(peer.addresses)
        guard (1...4).contains(peer.dns.count) else { throw BadPeer(field: "dns count") }
        guard peer.dns.allSatisfy({ isIPv4($0) || isIPv6($0) }) else { throw BadPeer(field: "dns") }
        guard Set(peer.allowedIPs) == fullTunnel else { throw BadPeer(field: "routes") }
        guard (0...65535).contains(peer.keepalive) else { throw BadPeer(field: "keepalive") }
    }

    public static func checkKey(_ value: String) throws {
        guard value.count == 44 else { throw BadPeer(field: "key length") }
        guard let raw = Data(base64Encoded: value), raw.count == 32, raw.contains(where: { $0 != 0 }) else { throw BadPeer(field: "key") }
    }

    private static func checkEndpoint(_ host: String) throws {
        guard !host.isEmpty, host.count <= 253 else { throw BadPeer(field: "endpoint") }
        if isIPv4(host) {
            guard !isInternalIPv4(host) else { throw BadPeer(field: "endpoint internal") }
            return
        }
        if isIPv6(host) {
            guard host != "::1", host != "::" else { throw BadPeer(field: "endpoint internal") }
            return
        }
        // Every label a hostname, and not all digits: "1.2.3" or "999.1.1.1" isn't an address or a name
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        guard labels.count >= 2, labels.allSatisfy(isHostLabel), let last = labels.last, !last.allSatisfy(\.isNumber) else { throw BadPeer(field: "endpoint") }
    }

    private static func isHostLabel(_ label: Substring) -> Bool {
        guard (1...63).contains(label.count), let first = label.first, let last = label.last, isASCIILetterOrDigit(first), isASCIILetterOrDigit(last) else { return false }
        return label.allSatisfy { isASCIILetterOrDigit($0) || $0 == "-" }
    }

    private static func isASCIILetterOrDigit(_ c: Character) -> Bool {
        c.isASCII && (c.isLetter || c.isNumber)
    }

    private static func checkAddresses(_ addresses: [String]) throws {
        guard (1...2).contains(addresses.count) else { throw BadPeer(field: "addresses") }
        var v4 = 0, v6 = 0
        for a in addresses {
            let parts = a.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
            guard parts.count == 2 else { throw BadPeer(field: "address") }
            if isIPv4(parts[0]), parts[1] == "32", isTunnelIPv4(parts[0]) {
                v4 += 1
            } else if isIPv6(parts[0]), parts[1] == "128" {
                v6 += 1
            } else {
                throw BadPeer(field: "address")
            }
        }
        guard v4 == 1, v6 <= 1 else { throw BadPeer(field: "addresses") }
    }

    public static func isIPv4(_ value: String) -> Bool {
        let parts = value.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 4 && parts.allSatisfy { p in
            guard (1...3).contains(p.count), p.allSatisfy({ $0.isASCII && $0.isNumber }), let n = Int(p) else { return false }
            return n <= 255 && (p.count == 1 || p.first != "0")
        }
    }

    private static func octets(_ ip: String) -> [Int] {
        ip.split(separator: ".").compactMap { Int($0) }
    }

    /// The private ranges a WireGuard pool lives in.
    private static func isTunnelIPv4(_ ip: String) -> Bool {
        let o = octets(ip)
        return o[0] == 10 || (o[0] == 172 && (16...31).contains(o[1])) || (o[0] == 192 && o[1] == 168) || (o[0] == 100 && (64...127).contains(o[1]))
    }

    private static func isInternalIPv4(_ ip: String) -> Bool {
        let o = octets(ip)
        return o[0] == 0 || o[0] == 127 || o[0] >= 224 || (o[0] == 169 && o[1] == 254) || isTunnelIPv4(ip)
    }

    public static func isIPv6(_ value: String) -> Bool {
        guard value.count <= 39, value.contains(":"), value.allSatisfy({ $0.isASCII && ($0.isHexDigit || $0 == ":") }), !value.contains(":::") else { return false }
        let doubles = value.components(separatedBy: "::").count - 1
        guard doubles <= 1 else { return false }
        let groups = value.components(separatedBy: "::").flatMap { $0.split(separator: ":") }
        guard groups.allSatisfy({ $0.count <= 4 }) else { return false }
        if doubles == 1 { return groups.count <= 7 }
        let all = value.split(separator: ":", omittingEmptySubsequences: false)
        return all.count == 8 && all.allSatisfy { !$0.isEmpty }
    }
}
