package com.ultron.assistant.ui

import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Surface
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ultron.assistant.R

/** One typeface (IBM Plex Sans) through Material 3's type scale, so sizes are roles, not per-screen
 *  guesses, and everything is in sp (it follows the system font size). Plex Mono is only for data:
 *  pairing codes, fingerprints, addresses. */
val Plex = FontFamily(
    Font(R.font.plex_sans_regular, FontWeight.Normal),
    Font(R.font.plex_sans_medium, FontWeight.Medium),
    Font(R.font.plex_sans_semibold, FontWeight.SemiBold),
)
val PlexMono = FontFamily(Font(R.font.plex_mono_regular, FontWeight.Normal))

private fun t(size: Int, line: Int, weight: FontWeight = FontWeight.Normal, track: Double = 0.0) =
    TextStyle(fontFamily = Plex, fontSize = size.sp, lineHeight = line.sp, fontWeight = weight, letterSpacing = track.sp)

private val typography = Typography(
    headlineMedium = t(28, 36, FontWeight.SemiBold),
    headlineSmall = t(24, 32, FontWeight.SemiBold),
    titleLarge = t(22, 28, FontWeight.Medium),
    titleMedium = t(16, 24, FontWeight.Medium, 0.15),
    titleSmall = t(14, 20, FontWeight.Medium, 0.1),
    bodyLarge = t(16, 24, track = 0.2),
    bodyMedium = t(14, 20, track = 0.2),
    bodySmall = t(12, 16, track = 0.3),
    labelLarge = t(14, 20, FontWeight.Medium, 0.1),
    labelMedium = t(12, 16, FontWeight.Medium, 0.4),
    labelSmall = t(11, 16, FontWeight.Medium, 0.4),
)

private val shapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp), small = RoundedCornerShape(10.dp), medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(20.dp), extraLarge = RoundedCornerShape(28.dp),
)

// Brand blue, as Material colour roles (both schemes checked for 4.5:1 text contrast).
private val dark = darkColorScheme(
    primary = Color(0xFF5CC4FF), onPrimary = Color(0xFF03111F),
    primaryContainer = Color(0xFF12395C), onPrimaryContainer = Color(0xFFCFEAFF),
    secondary = Color(0xFF9DB8D6), onSecondary = Color(0xFF06131F),
    background = Color(0xFF070E1A), onBackground = Color(0xFFE6F1FB),
    surface = Color(0xFF0C1626), onSurface = Color(0xFFE6F1FB),
    surfaceVariant = Color(0xFF14243A), onSurfaceVariant = Color(0xFF9DB3C9),
    outline = Color(0xFF3A526D), outlineVariant = Color(0xFF1F324A),
    error = Color(0xFFFF8A84), onError = Color(0xFF2A0705),
    secondaryContainer = Color(0xFF12395C), onSecondaryContainer = Color(0xFFCFEAFF),
    surfaceContainerLowest = Color(0xFF050B14), surfaceContainerLow = Color(0xFF0A1424),
    surfaceContainer = Color(0xFF0C1626), surfaceContainerHigh = Color(0xFF101D31), surfaceContainerHighest = Color(0xFF14243A),
    surfaceTint = Color(0xFF5CC4FF),
)
private val light = lightColorScheme(
    primary = Color(0xFF0A6BA8), onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFCDE7FA), onPrimaryContainer = Color(0xFF00263F),
    secondary = Color(0xFF475B70), onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFF4F8FC), onBackground = Color(0xFF0B1A2B),
    surface = Color(0xFFFFFFFF), onSurface = Color(0xFF0B1A2B),
    surfaceVariant = Color(0xFFE3ECF5), onSurfaceVariant = Color(0xFF475B70),
    outline = Color(0xFF8CA2B8), outlineVariant = Color(0xFFCBD8E5),
    error = Color(0xFFB3261E), onError = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFCDE7FA), onSecondaryContainer = Color(0xFF00263F),
    surfaceContainerLowest = Color(0xFFFFFFFF), surfaceContainerLow = Color(0xFFF0F5FA),
    surfaceContainer = Color(0xFFEAF1F8), surfaceContainerHigh = Color(0xFFE3ECF5), surfaceContainerHighest = Color(0xFFDCE7F2),
    surfaceTint = Color(0xFF0A6BA8),
)

/** Every screen sits on a Surface (without one, text would default to black on a dark window) and
 *  inside the safe area (Android 15 draws apps edge to edge, under the bars and the keyboard).
 *  Dark and light follow the system; dark is designed, not inverted. */
@Composable
fun UltronTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme()) dark else light
    MaterialTheme(colorScheme = scheme, typography = typography, shapes = shapes) {
        Surface(modifier = Modifier.fillMaxSize(), color = scheme.background, contentColor = scheme.onBackground) {
            Box(Modifier.fillMaxSize().safeDrawingPadding()) { content() }
        }
    }
}

/** Edge to edge; the bar icons follow the theme (light icons on dark, dark icons on light). */
fun ComponentActivity.edgeToEdge() = enableEdgeToEdge()
