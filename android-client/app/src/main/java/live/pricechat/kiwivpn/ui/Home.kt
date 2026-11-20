package live.pricechat.kiwivpn.ui

import android.provider.Settings
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import live.pricechat.kiwivpn.R
import live.pricechat.kiwivpn.UiState
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.model.StatusKind
import live.pricechat.kiwivpn.model.accountStatus
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.vpn.ConnectionState

/** The word the person sees. Protected appears only with a real handshake: nothing else can reach ConnectionState.Connected. */
fun statusWord(state: ConnectionState): String = when (state) {
    is ConnectionState.Connected -> "Protected"
    is ConnectionState.Reconnecting -> "Reconnecting"
    is ConnectionState.Connecting -> "Connecting"
    is ConnectionState.Disconnecting -> "Disconnecting"
    is ConnectionState.Disconnected -> "Unprotected"
    is ConnectionState.Failed -> "Connection failed"
}

fun flagOf(countryCode: String): String {
    val code = countryCode.uppercase()
    if (code.length != 2 || code.any { it !in 'A'..'Z' }) return ""
    return String(Character.toChars(0x1F1E6 + (code[0] - 'A'))) + String(Character.toChars(0x1F1E6 + (code[1] - 'A')))
}

fun durationText(ms: Long): String {
    val s = (ms / 1000).coerceAtLeast(0)
    return "%02d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60)
}

@Composable
fun BetaChip() {
    Text(
        "Public beta",
        color = Kiwi.Warn,
        style = MaterialTheme.typography.labelSmall,
        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(Kiwi.Warn.copy(alpha = 0.12f)).padding(horizontal = 10.dp, vertical = 3.dp),
    )
}

/** Respects Settings > Accessibility > Remove animations, which sets this scale to 0. */
@Composable
private fun reducedMotion(): Boolean {
    val context = LocalContext.current
    return remember { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
}

@Composable
fun HomeScreen(ui: UiState, state: ConnectionState, onConnect: () -> Unit, onDisconnect: () -> Unit, onStartTrial: () -> Unit, onLocations: () -> Unit) {
    val status = accountStatus(ui.account)
    val haptics = LocalHapticFeedback.current
    // With no answer from Price Chat there's still a server to try: the last one used, with saved details. The server decides whether its key still works.
    val canConnect = ui.selected != null && (status.canConnect || ui.account == null)
    val busy = state is ConnectionState.Disconnecting
    val turnOff = state.isActive

    // A haptic for what really happened, not for the press alone.
    LaunchedEffect(state::class) {
        if (state is ConnectionState.Connected) haptics.performHapticFeedback(HapticFeedbackType.LongPress)
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 18.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("KIWI VPN", style = MaterialTheme.typography.titleMedium, color = Kiwi.Text)
            BetaChip()
        }
        Spacer(Modifier.height(30.dp))
        ConnectButton(
            state = state,
            enabled = !busy && (turnOff || canConnect),
            onClick = {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                if (turnOff) onDisconnect() else onConnect()
            },
        )
        Spacer(Modifier.height(22.dp))
        Text(
            statusWord(state).uppercase(),
            style = MaterialTheme.typography.displaySmall,
            textAlign = TextAlign.Center,
            color = when (state) {
                is ConnectionState.Connected -> Kiwi.LimeBright
                is ConnectionState.Failed -> Kiwi.Warn
                is ConnectionState.Reconnecting -> Kiwi.Warn
                else -> Kiwi.Text
            },
        )
        Spacer(Modifier.height(6.dp))
        Text(detailOf(state), color = Kiwi.Soft, textAlign = TextAlign.Center)
        (state as? ConnectionState.Connected)?.let { Session(it) }

        Spacer(Modifier.height(22.dp))
        ServerRow(ui, state, onLocations)
        Spacer(Modifier.height(12.dp))
        AccountRow(status.label, status.detail, status.kind == StatusKind.TRIAL || status.kind == StatusKind.PAID || status.kind == StatusKind.UNLIMITED)
        if (status.canStartTrial) {
            Spacer(Modifier.height(12.dp))
            Button(onClick = onStartTrial, modifier = Modifier.fillMaxWidth().height(50.dp)) { Text("Start free trial") }
        }
        ui.error?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, color = Kiwi.Warn, textAlign = TextAlign.Center)
        }
        Spacer(Modifier.height(18.dp))
        Text(
            "Public beta: there's no kill switch in this app, and DNS and IPv6 leak testing isn't finished. See Settings for Android's own Always-on VPN.",
            style = MaterialTheme.typography.bodySmall,
            color = Kiwi.Muted,
            textAlign = TextAlign.Center,
        )
    }
}

private fun detailOf(state: ConnectionState): String = when (state) {
    is ConnectionState.Connected -> state.notice ?: "Connected to ${state.server.name}"
    is ConnectionState.Reconnecting -> "The server isn't answering. Trying again. Traffic waits rather than leaving outside the tunnel."
    is ConnectionState.Connecting -> when (state.step) {
        ConnectionState.Connecting.Step.PREPARING -> "Getting ${state.target.name} ready"
        ConnectionState.Connecting.Step.STARTING -> "Starting the tunnel"
        ConnectionState.Connecting.Step.WAITING_FOR_SERVER -> "Waiting for ${state.target.name} to answer"
    }
    is ConnectionState.Disconnecting -> "Closing the tunnel"
    is ConnectionState.Disconnected -> state.notice ?: "Your traffic isn't going through Kiwi VPN."
    is ConnectionState.Failed -> state.message
}

@Composable
private fun Session(state: ConnectionState.Connected) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(state.link.sinceMs) {
        while (true) {
            now = System.currentTimeMillis()
            delay(1000)
        }
    }
    Spacer(Modifier.height(6.dp))
    Text(durationText(now - state.link.sinceMs), color = Kiwi.Text, fontWeight = FontWeight.SemiBold)
    if (state.link.routing.mode != RoutingMode.ALL_APPS) {
        Text(RoutingPolicy.describe(state.link.routing), style = MaterialTheme.typography.bodySmall, color = Kiwi.LimeBright, textAlign = TextAlign.Center)
    }
    state.link.publicIp?.let { ip ->
        Text(if (state.link.ipMatchesServer) "Public IP: $ip (verified: the server's)" else "Public IP: $ip (not the server's address)", style = MaterialTheme.typography.bodySmall, color = if (state.link.ipMatchesServer) Kiwi.Soft else Kiwi.Warn, textAlign = TextAlign.Center)
    }
}

@Composable
private fun ServerRow(ui: UiState, state: ConnectionState, onLocations: () -> Unit) {
    val server: ServerRef? = when (state) {
        is ConnectionState.Connected -> state.server
        is ConnectionState.Reconnecting -> state.server
        is ConnectionState.Connecting -> state.target
        else -> ui.selected?.let { ServerRef(it.id, it.name, it.countryCode) }
    }
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Kiwi.Forest).clickable(onClick = onLocations).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(if (server != null) "${flagOf(server.countryCode)}  ${server.name}".trim() else if (ui.checked) "No server available" else "Loading servers", style = MaterialTheme.typography.titleMedium)
        Text("Change", color = Kiwi.LimeBright, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun AccountRow(label: String, detail: String?, good: Boolean) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Kiwi.Forest).padding(14.dp)) {
        Text(label, color = if (good) Kiwi.Text else Kiwi.Warn, style = MaterialTheme.typography.titleMedium)
        detail?.let { Text(it, color = Kiwi.Soft, style = MaterialTheme.typography.bodySmall) }
    }
}

/** It moves only in ways that say something true: a slow breath when off, a turning ring while connecting, a steady glow when a handshake has been seen. */
@Composable
private fun ConnectButton(state: ConnectionState, enabled: Boolean, onClick: () -> Unit) {
    val still = reducedMotion()
    val motion = rememberInfiniteTransition(label = "connect")
    val breath by motion.animateFloat(0.97f, 1.03f, infiniteRepeatable(tween(2600), RepeatMode.Reverse), label = "breath")
    val spin by motion.animateFloat(0f, 360f, infiniteRepeatable(tween(1400, easing = LinearEasing)), label = "spin")
    val on = state is ConnectionState.Connected
    val working = state is ConnectionState.Connecting || state is ConnectionState.Reconnecting || state is ConnectionState.Disconnecting
    val ring by animateColorAsState(
        when {
            on -> Kiwi.LimeBright
            state is ConnectionState.Failed -> Kiwi.Warn
            working -> Kiwi.Lime
            else -> Kiwi.Forest3
        },
        tween(if (still) 0 else 450),
        label = "ring",
    )
    val label = if (state.isActive) "Disconnect" else "Connect"
    Box(
        Modifier
            .size(196.dp)
            .scale(if (!still && !on && !working) breath else 1f)
            .clip(CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClickLabel = label, onClick = onClick)
            .semantics { contentDescription = "$label. ${statusWord(state)}" },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.fillMaxSize()) {
            val width = 6.dp.toPx()
            val inset = width / 2
            val arc = Size(size.width - width, size.height - width)
            drawCircle(color = if (on) Kiwi.Forest2 else Kiwi.Forest, radius = size.minDimension / 2 - width)
            if (working && !still) {
                drawArc(Kiwi.Forest3, 0f, 360f, false, topLeft = Offset(inset, inset), size = arc, style = Stroke(width))
                drawArc(ring, spin, 100f, false, topLeft = Offset(inset, inset), size = arc, style = Stroke(width))
            } else {
                drawArc(ring, 0f, 360f, false, topLeft = Offset(inset, inset), size = arc, style = Stroke(width))
            }
            if (on) drawCircle(Color(0x22BEF264), radius = size.minDimension / 2 - width * 2)
        }
        Image(painterResource(R.drawable.kiwi), contentDescription = null, Modifier.size(112.dp).alpha(if (on) 1f else if (enabled) 0.72f else 0.4f))
    }
}
