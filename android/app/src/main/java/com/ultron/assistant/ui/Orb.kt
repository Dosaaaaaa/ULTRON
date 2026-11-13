package com.ultron.assistant.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

enum class OrbMode { Offline, Online, Busy }

/** The phone's version of the desktop's blue orb: a glow, a slowly turning ring, and a core whose
 *  brightness says what is going on (dim = not connected, steady = connected, quick = thinking). */
@Composable
fun Orb(mode: OrbMode, size: Dp = 120.dp, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "orb")
    val spin by transition.animateFloat(
        0f, 360f,
        infiniteRepeatable(tween(if (mode == OrbMode.Busy) 2200 else 9000, easing = LinearEasing)),
        label = "spin",
    )
    val pulse by transition.animateFloat(
        0.85f, 1f,
        infiniteRepeatable(tween(if (mode == OrbMode.Busy) 700 else 2400), RepeatMode.Reverse),
        label = "pulse",
    )
    val blue = if (mode == OrbMode.Offline) Color(0xFF3B5A78) else Color(0xFF38BDF8)
    val glow = when (mode) { OrbMode.Offline -> 0.18f; OrbMode.Online -> 0.42f * pulse; OrbMode.Busy -> 0.6f * pulse }
    Canvas(modifier.size(size)) {
        val c = Offset(this.size.width / 2, this.size.height / 2)
        val r = this.size.minDimension / 2
        drawCircle(Brush.radialGradient(listOf(blue.copy(alpha = glow), Color.Transparent), c, r), r, c)
        val ring = Stroke(width = r * 0.045f, cap = StrokeCap.Round)
        val box = Size(r * 1.4f, r * 1.4f)
        val tl = Offset(c.x - box.width / 2, c.y - box.height / 2)
        for (i in 0 until 3) {
            drawArc(blue.copy(alpha = if (mode == OrbMode.Offline) 0.35f else 0.9f - i * 0.2f), spin * (1 + i * 0.5f) + i * 120f, 70f + i * 25f, false, tl, box, style = ring)
        }
        drawCircle(blue.copy(alpha = if (mode == OrbMode.Offline) 0.4f else 1f), r * 0.2f * (if (mode == OrbMode.Offline) 1f else pulse + 0.1f), c)
    }
}
