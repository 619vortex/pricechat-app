import KiwiCore
import SwiftUI

struct HomeView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var connection: ConnectionCoordinator

    private var status: AccountStatus { AccountStatus.of(model.account) }

    private var canConnect: Bool {
        // With no answer from Price Chat there's still a server to try, the last one used. The server decides whether its key still works.
        model.selected != nil && (status.canConnect || model.account == nil)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                HStack {
                    Text("KIWI VPN").font(.headline)
                    Spacer()
                    BetaChip()
                }
                ConnectButton(state: connection.state, enabled: isEnabled) {
                    Task {
                        if connection.state.isActive { await model.disconnect() } else { await model.connect() }
                    }
                }.padding(.vertical, 14)
                Text(connection.state.word.uppercased())
                    .font(.largeTitle.weight(.black))
                    .foregroundStyle(wordColor)
                    .accessibilityLabel(connection.state.word)
                Text(detail).foregroundStyle(KiwiColor.soft).multilineTextAlignment(.center)
                if case .connected(_, let link, _) = connection.state { Session(link: link) }

                KiwiCard {
                    HStack {
                        Text("\(flag(serverShown?.countryCode ?? ""))  \(serverShown?.name ?? (model.checked ? "No server available" : "Loading servers"))").font(.headline)
                        Spacer()
                    }
                }
                KiwiCard {
                    Text(status.label).font(.headline).foregroundStyle(status.canConnect ? KiwiColor.text : KiwiColor.warn)
                    if let detail = status.detail { Text(detail).font(.footnote).foregroundStyle(KiwiColor.soft) }
                    if status.canStartTrial {
                        Button { Task { await model.startTrial() } } label: { Text("Start free trial").frame(maxWidth: .infinity) }
                            .buttonStyle(.borderedProminent).foregroundStyle(KiwiColor.ink)
                    }
                }
                if let error = model.error { Text(error).foregroundStyle(KiwiColor.warn).multilineTextAlignment(.center) }
                Text("Public beta: there's no kill switch, and DNS and IPv6 leak testing isn't finished.")
                    .font(.footnote).foregroundStyle(KiwiColor.muted).multilineTextAlignment(.center)
            }.padding(20)
        }
        .sensoryFeedback(.success, trigger: isConnected)
    }

    private var isConnected: Bool {
        if case .connected = connection.state { return true }
        return false
    }

    private var isEnabled: Bool {
        if case .disconnecting = connection.state { return false }
        return connection.state.isActive || canConnect
    }

    private var serverShown: ServerRef? {
        connection.state.isActive ? connection.state.server : model.selected
    }

    private var wordColor: Color {
        switch connection.state {
        case .connected: return KiwiColor.limeBright
        case .failed, .reconnecting: return KiwiColor.warn
        default: return KiwiColor.text
        }
    }

    private var detail: String {
        switch connection.state {
        case .connected(let server, _, let notice): return notice ?? "Connected to \(server.name)"
        case .reconnecting: return "The server isn't answering. Trying again."
        case .connecting(let target, let step):
            switch step {
            case .preparing: return "Getting \(target.name) ready"
            case .starting: return "Starting the tunnel"
            case .waitingForServer: return "Waiting for \(target.name) to answer"
            }
        case .disconnecting: return "Closing the tunnel"
        case .disconnected(let notice): return notice ?? "Your traffic isn't going through Kiwi VPN."
        case .failed(let message, _): return message
        }
    }
}

/// The timer counts from when iOS says the connection came up, so it stays right after the app has been closed and reopened.
struct Session: View {
    let link: Link

    var body: some View {
        VStack(spacing: 2) {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                Text(Self.duration(context.date.timeIntervalSince(link.since))).font(.headline.monospacedDigit())
            }
            if let ip = link.publicIP {
                Text(link.ipMatchesServer ? "Public IP: \(ip) (verified: the server's)" : "Public IP: \(ip) (not the server's address)")
                    .font(.footnote).foregroundStyle(link.ipMatchesServer ? KiwiColor.soft : KiwiColor.warn)
            }
        }
    }

    static func duration(_ interval: TimeInterval) -> String {
        let s = max(0, Int(interval))
        return String(format: "%02d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }
}

/// It moves only to say something true: a slow breath when off, a turning ring while connecting, a steady glow when a handshake has been seen. Honours Reduce Motion.
struct ConnectButton: View {
    let state: ConnectionState
    let enabled: Bool
    let action: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false
    @State private var turning = false

    private var on: Bool {
        if case .connected = state { return true }
        return false
    }

    private var working: Bool {
        switch state {
        case .connecting, .reconnecting, .disconnecting: return true
        default: return false
        }
    }

    private var ring: Color {
        if on { return KiwiColor.limeBright }
        if case .failed = state { return KiwiColor.warn }
        return working ? KiwiColor.lime : KiwiColor.forest3
    }

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().fill(on ? KiwiColor.forest2 : KiwiColor.forest)
                Circle().stroke(KiwiColor.forest3, lineWidth: 6)
                Circle()
                    .trim(from: 0, to: working && !reduceMotion ? 0.3 : 1)
                    .stroke(ring, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                    .rotationEffect(.degrees(turning ? 360 : 0))
                Image("Kiwi").resizable().scaledToFit().frame(width: 112, height: 112).opacity(on ? 1 : (enabled ? 0.72 : 0.4))
            }
            .frame(width: 196, height: 196)
            .scaleEffect(!reduceMotion && !on && !working && breathing ? 1.03 : 1)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel("\(state.isActive ? "Disconnect" : "Connect"). \(state.word)")
        .onAppear { update() }
        .onChange(of: working) { update() }
    }

    private func update() {
        guard !reduceMotion else { return }
        if working {
            withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) { turning = true }
        } else {
            withAnimation(.default) { turning = false }
            withAnimation(.easeInOut(duration: 2.6).repeatForever(autoreverses: true)) { breathing = true }
        }
    }
}
