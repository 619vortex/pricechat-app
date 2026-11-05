package live.pricechat.kiwivpn.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// The same colours as pricechat.live/vpn and the Windows app: frontend/src/website/vpn.css and windows-client/app/src/app.css.
object Kiwi {
    val Ground = Color(0xFF070B09)
    val Forest = Color(0xFF0D1D15)
    val Forest2 = Color(0xFF12281C)
    val Forest3 = Color(0xFF1A3726)
    val Line = Color(0x1CD6F5AA)
    val Lime = Color(0xFFA3E635)
    val LimeBright = Color(0xFFBEF264)
    val Ink = Color(0xFF0B1406)
    val Warn = Color(0xFFFF9F5A)
    val Text = Color(0xFFF6F8F4)
    val Soft = Color(0xFFBCC7BF)
    val Muted = Color(0xFF84918A)
}

@Composable
fun KiwiTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Kiwi.Lime,
            onPrimary = Kiwi.Ink,
            secondary = Kiwi.LimeBright,
            onSecondary = Kiwi.Ink,
            background = Kiwi.Ground,
            onBackground = Kiwi.Text,
            surface = Kiwi.Ground,
            onSurface = Kiwi.Text,
            surfaceVariant = Kiwi.Forest,
            onSurfaceVariant = Kiwi.Soft,
            surfaceContainer = Kiwi.Forest,
            surfaceContainerHigh = Kiwi.Forest2,
            outline = Kiwi.Forest3,
            error = Kiwi.Warn,
            onError = Kiwi.Ink,
        ),
        typography = Typography(
            displaySmall = TextStyle(fontWeight = FontWeight.Black, fontSize = 34.sp, lineHeight = 38.sp),
            headlineSmall = TextStyle(fontWeight = FontWeight.ExtraBold, fontSize = 24.sp, lineHeight = 30.sp),
            titleMedium = TextStyle(fontWeight = FontWeight.Bold, fontSize = 16.sp, lineHeight = 22.sp),
            bodyMedium = TextStyle(fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 21.sp),
            bodySmall = TextStyle(fontWeight = FontWeight.Normal, fontSize = 13.sp, lineHeight = 18.sp),
            labelSmall = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 12.sp, lineHeight = 16.sp),
        ),
        content = content,
    )
}
