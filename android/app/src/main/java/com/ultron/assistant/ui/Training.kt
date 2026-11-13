package com.ultron.assistant.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.ultron.assistant.core.WakeTraining
import com.ultron.assistant.service.VoskListener

private const val ATTEMPTS = 6

/** Voice training: say the wake name [ATTEMPTS] times. Shows what the listener wrote each time, then
 *  keeps the variants that are consistently its spelling of YOUR voice saying the name. The microphone is
 *  open only while this screen is, and nothing is stored except the learned spellings. */
@Composable
fun TrainScreen(name: String, existing: List<String>, onSave: (List<String>) -> Unit, onClose: () -> Unit) {
    val context = LocalContext.current
    val attempts = remember { mutableStateListOf<String>() }
    var status by remember { mutableStateOf("Loading the listener…") }
    var round by remember { mutableIntStateOf(0) }
    val listener = remember { VoskListener(context) }
    val shown = name.replaceFirstChar { it.uppercase() }

    DisposableEffect(round) {
        attempts.clear()
        status = "Loading the listener…"
        listener.load(
            indian = com.ultron.assistant.data.Store(context).speechModel == "in",
            onReady = {
                status = "Listening. Say “$shown” now."
                listener.start(onText = { text -> if (attempts.size < ATTEMPTS) attempts += text }, onFail = { status = "The microphone failed: $it" }, echoCancel = com.ultron.assistant.data.Store(context).bargeIn)
            },
            onFail = { status = "Could not load the listener: $it" },
        )
        onDispose { listener.stop() }
    }
    val done = attempts.size >= ATTEMPTS
    val result = if (done) WakeTraining.learn(name, attempts.toList(), existing) else null
    if (done) listener.stop()

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Train “$shown”", style = MaterialTheme.typography.headlineSmall)
        if (result == null) {
            Text(status, style = MaterialTheme.typography.titleMedium)
            Text("Say “$shown” in your normal voice, about an arm's length away, pausing between each. ${attempts.size} of $ATTEMPTS heard.", style = MaterialTheme.typography.bodyMedium)
        } else {
            Text("Heard correctly ${result.hits} of ${result.total}", style = MaterialTheme.typography.titleLarge)
            Text(
                when {
                    result.learned.isNotEmpty() -> "It writes your “$shown” as ${result.learned.joinToString { "“$it”" }} too. Save, and those will wake it as well."
                    result.hits == result.total -> "Perfect: it understands you. If it still misses in daily use, the cause is distance or noise, not your voice."
                    result.hits >= 1 -> "Some tries were missed with no pattern to learn. Try again closer to the phone, or choose another name in Settings."
                    else -> "It did not hear the name at all. Check the microphone permission, move closer, or pick another name."
                },
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        Text("What it wrote for each try", style = MaterialTheme.typography.labelLarge)
        if (attempts.isEmpty()) Text("Nothing yet.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        attempts.forEachIndexed { i, t ->
            Text("${i + 1}.  “$t”", style = MaterialTheme.typography.bodyMedium)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
            if (result != null) {
                Button(onClick = { onSave(result.all); onClose() }) { Text("Save") }
                OutlinedButton(onClick = { round++ }) { Text("Try again") }
            }
            OutlinedButton(onClick = onClose) { Text(if (result != null) "Close" else "Cancel") }
        }
    }
}
