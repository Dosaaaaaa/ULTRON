package com.ultron.assistant.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.ultron.assistant.R
import com.ultron.assistant.core.Display
import com.ultron.assistant.core.Fingerprint
import com.ultron.assistant.core.ReminderItem
import com.ultron.assistant.data.Pairing
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

// ====================================================================== pairing
@Composable
fun PairScreen(
    busy: Boolean,
    error: String?,
    defaultName: String,
    onScan: () -> Unit,
    onManual: (address: String, code: String, fingerprint: String, name: String) -> Unit,
    onBack: (() -> Unit)? = null,
) {
    var manual by remember { mutableStateOf(false) }
    var address by remember { mutableStateOf("https://") }
    var code by remember { mutableStateOf("") }
    var fingerprint by remember { mutableStateOf("") }
    var name by remember { mutableStateOf(defaultName) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        if (onBack != null) Row(Modifier.fillMaxWidth()) { TextButton(onClick = onBack) { Text("Back") } }
        Spacer(Modifier.height(8.dp))
        Orb(OrbMode.Offline, 140.dp)
        Text("Connect to your computer", style = MaterialTheme.typography.headlineSmall)
        Text("Optional. Reminders and calls already work on this phone. Connecting adds AI answers from your computer.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        // A real sequence, so it is numbered.
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("1.  On your computer, open ULTRON and choose Phone.", style = MaterialTheme.typography.bodyMedium)
            Text("2.  Create a pairing code. A QR code appears.", style = MaterialTheme.typography.bodyMedium)
            Text("3.  Scan it here.", style = MaterialTheme.typography.bodyMedium)
        }
        Button(onClick = onScan, enabled = !busy, modifier = Modifier.fillMaxWidth().height(56.dp)) {
            Text(if (busy) "Pairing…" else "Scan QR code", style = MaterialTheme.typography.titleMedium)
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) }
        TextButton(onClick = { manual = !manual }) { Text(if (manual) "Hide manual entry" else "No camera? Enter the details by hand") }
        if (manual) {
            OutlinedTextField(address, { address = it }, label = { Text("Server address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(code, { code = it.uppercase() }, label = { Text("Pairing code") }, placeholder = { Text("ABCD-EFGH") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(fingerprint, { fingerprint = it }, label = { Text("Certificate fingerprint") }, minLines = 2, textStyle = MaterialTheme.typography.bodySmall.copy(fontFamily = PlexMono), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(name, { name = it }, label = { Text("Name for this phone") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(onClick = { onManual(address, code, fingerprint, name) }, enabled = !busy && code.length >= 8, modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("Pair") }
        }
    }
}

@Composable
fun ConfirmLinkDialog(address: String, fingerprint: String?, onConfirm: () -> Unit, onCancel: () -> Unit) {
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text("Pair with this computer?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(address, style = MaterialTheme.typography.titleSmall.copy(fontFamily = PlexMono))
                if (fingerprint != null) {
                    Text("Certificate ${Fingerprint.pretty(fingerprint).take(23)}…", style = MaterialTheme.typography.bodySmall.copy(fontFamily = PlexMono))
                }
                Text("Only continue if you just scanned this from your own ULTRON.", style = MaterialTheme.typography.bodyMedium)
            }
        },
        confirmButton = { Button(onClick = onConfirm) { Text("Pair") } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Cancel") } },
    )
}

// ====================================================================== chat
@Composable
fun ChatScreen(
    lines: List<ChatLine>,
    busy: Boolean,
    note: String?,
    canRetry: Boolean,
    listening: Boolean,
    voiceAvailable: Boolean,
    partial: String,
    canFixMic: Boolean,
    onFixMic: () -> Unit,
    onSend: (String) -> Unit,
    onRetry: () -> Unit,
    onMicClick: () -> Unit,
    onNewChat: () -> Unit,
    status: String,
) {
    var input by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    LaunchedEffect(lines.size, busy) { if (lines.isNotEmpty()) listState.animateScrollToItem(lines.size) }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Orb(if (busy || listening) OrbMode.Busy else OrbMode.Online, 44.dp)
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text("ULTRON", style = MaterialTheme.typography.titleMedium)
                Text(
                    if (listening) (if (partial.isNotBlank()) "“$partial”" else "Listening… speak now") else if (busy) "Thinking…" else status,
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            if (lines.isNotEmpty()) TextButton(onClick = onNewChat) { Text("New chat") }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = listState, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (lines.isEmpty()) item { EmptyChat() }
            items(lines) { Bubble(it) }
            if (busy) item { Bubble(ChatLine(false, "…")) }
        }
        if (note != null) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(note, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                if (canFixMic) TextButton(onClick = onFixMic) { Text("Fix permission") }
                else if (canRetry) TextButton(onClick = onRetry) { Text("Try again") }
            }
        }
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (voiceAvailable) MicButton(listening, onMicClick)
            OutlinedTextField(
                input, { input = it }, modifier = Modifier.weight(1f), placeholder = { Text("Message ULTRON") }, maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { onSend(input); input = "" }),
                shape = RoundedCornerShape(26.dp),
            )
            Button(
                onClick = { onSend(input); input = "" }, enabled = input.isNotBlank() && !busy,
                modifier = Modifier.size(56.dp), shape = CircleShape, contentPadding = PaddingValues(0.dp),
            ) { Icon(Icons.Filled.Send, contentDescription = "Send message") }
        }
    }
}

/** Tap to talk: tap once, speak, and it stops by itself when you pause (tap again to stop sooner). The
 *  ring pulses while the microphone is live. */
@Composable
private fun MicButton(listening: Boolean, onClick: () -> Unit) {
    val pulse by rememberInfiniteTransition(label = "mic").animateFloat(
        0.35f, 0.9f, infiniteRepeatable(tween(650), RepeatMode.Reverse), label = "ring",
    )
    val container = if (listening) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
    val tint = if (listening) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.primary
    Box(
        Modifier.size(56.dp)
            .then(if (listening) Modifier.background(MaterialTheme.colorScheme.primary.copy(alpha = pulse * 0.35f), CircleShape) else Modifier)
            .padding(if (listening) 4.dp else 0.dp)
            .clip(CircleShape).background(container)
            .semantics { contentDescription = if (listening) "Stop listening" else "Talk to ULTRON" }
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Icon(painterResource(R.drawable.ic_mic), contentDescription = null, tint = tint, modifier = Modifier.size(24.dp)) }
}

@Composable
private fun EmptyChat() {
    Column(Modifier.fillMaxWidth().padding(vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Ask anything.", style = MaterialTheme.typography.titleMedium)
        Text(
            "Try “remind me in 10 minutes to stretch” or “call Mum”, typed or after tapping the microphone. Both work on this phone, with no computer needed.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun Bubble(line: ChatLine) {
    val mine = line.mine
    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (mine) Alignment.End else Alignment.Start, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Surface(
            color = if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
            contentColor = if (mine) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
            shape = RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp, bottomStart = if (mine) 18.dp else 4.dp, bottomEnd = if (mine) 4.dp else 18.dp),
            modifier = Modifier.widthIn(max = 320.dp),
        ) { Text(line.text, Modifier.padding(horizontal = 14.dp, vertical = 10.dp), style = MaterialTheme.typography.bodyLarge) }
        line.actions.forEach {
            Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(10.dp), modifier = Modifier.widthIn(max = 320.dp)) {
                Text(it.summary, Modifier.padding(horizontal = 10.dp, vertical = 6.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

// ====================================================================== reminders
@Composable
fun RemindersScreen(
    items: List<ReminderItem>,
    busy: Boolean,
    note: String?,
    adding: Boolean,
    onDismissAdd: () -> Unit,
    onRefresh: () -> Unit,
    onAdd: (String, Instant) -> Unit,
    onCancel: (String) -> Unit,
) {
    val now = Instant.now()
    val zone = ZoneId.systemDefault()
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Reminders", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            IconButton(onClick = onRefresh) { Icon(Icons.Filled.Refresh, "Refresh reminders") }
        }
        if (note != null) {
            Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(note, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = onRefresh) { Text("Try again") }
            }
        }
        when {
            busy && items.isEmpty() -> Skeleton()
            items.isEmpty() && note == null -> Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Nothing is waiting.", style = MaterialTheme.typography.titleMedium)
                Text("Add one with the button below, or just ask ULTRON: “remind me tomorrow at 9am to call Asha”.", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
            }
        }
        LazyColumn(contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 96.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(items, key = { it.id }) { r ->
                Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(14.dp)) {
                    Row(Modifier.fillMaxWidth().padding(start = 16.dp, top = 6.dp, bottom = 6.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(r.text, style = MaterialTheme.typography.bodyLarge)
                            Text("${Display.when_(r.dueAt, now, zone)}  ·  ${Display.distance(r.dueAt, now)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                        }
                        IconButton(onClick = { onCancel(r.id) }) { Icon(Icons.Filled.Delete, "Remove reminder: ${r.text}") }
                    }
                }
            }
        }
    }
    if (adding) AddReminderDialog(onDismiss = onDismissAdd, onAdd = onAdd)
}

@Composable
private fun Skeleton() {
    val a by rememberInfiniteTransition(label = "skeleton").animateFloat(0.35f, 0.8f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(3) {
            Box(Modifier.fillMaxWidth().height(64.dp).alpha(a).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(14.dp)))
        }
    }
}

@Composable
private fun AddReminderDialog(onDismiss: () -> Unit, onAdd: (String, Instant) -> Unit) {
    val context = LocalContext.current
    var text by remember { mutableStateOf("") }
    var due by remember { mutableStateOf(ZonedDateTime.now().plusMinutes(30).withSecond(0).withNano(0)) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New reminder") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(text, { text = it }, label = { Text("What should I remind you of?") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text(Display.when_(due.toInstant().toString(), Instant.now(), due.zone), style = MaterialTheme.typography.titleSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = {
                        DatePickerDialog(context, { _, y, m, d -> due = due.withYear(y).withMonth(m + 1).withDayOfMonth(d) }, due.year, due.monthValue - 1, due.dayOfMonth).show()
                    }) { Text("Date") }
                    OutlinedButton(onClick = {
                        TimePickerDialog(context, { _, h, min -> due = due.withHour(h).withMinute(min) }, due.hour, due.minute, true).show()
                    }) { Text("Time") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("10 min" to 10L, "1 hour" to 60L, "Tomorrow" to 1440L).forEach { (label, mins) ->
                        OutlinedButton(onClick = { due = ZonedDateTime.now().plusMinutes(mins).withSecond(0).withNano(0) }, contentPadding = PaddingValues(horizontal = 12.dp)) { Text(label) }
                    }
                }
                if (due.toInstant().isBefore(Instant.now())) Text("That time has passed.", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { Button(onClick = { onAdd(text, due.toInstant()) }, enabled = text.isNotBlank() && due.toInstant().isAfter(Instant.now())) { Text("Add reminder") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

// ====================================================================== settings
data class PermissionRow(val title: String, val why: String, val granted: Boolean, val onAllow: () -> Unit)

@Composable
fun SettingsScreen(
    pairing: Pairing?,
    connected: Boolean,
    detail: String,
    keepConnected: Boolean,
    permissions: List<PermissionRow>,
    confirmCalls: Boolean,
    guessTimes: Boolean,
    speakReplies: Boolean,
    canAutoDial: Boolean,
    handsFree: Boolean,
    soundOnWake: Boolean,
    onSoundOnWake: (Boolean) -> Unit,
    onPlaySound: () -> Unit,
    musicApps: List<Pair<String, String>>,
    musicApp: String,
    onMusicApp: (String) -> Unit,
    trainedCount: Int,
    onTrain: () -> Unit,
    wakeName: String,
    onWakeName: (String) -> Unit,
    overlayAllowed: Boolean,
    onlineCommands: Boolean,
    onOnlineCommands: (Boolean) -> Unit,
    bargeIn: Boolean,
    onBargeIn: (Boolean) -> Unit,
    wakeOverMusic: Boolean,
    onWakeOverMusic: (Boolean) -> Unit,
    speechModel: String,
    hasIndianModel: Boolean,
    onSpeechModel: (String) -> Unit,
    onHandsFree: (Boolean) -> Unit,
    onOverlay: () -> Unit,
    onConfirmCalls: (Boolean) -> Unit,
    onGuessTimes: (Boolean) -> Unit,
    onSpeakReplies: (Boolean) -> Unit,
    onConnect: () -> Unit,
    onKeepConnected: (Boolean) -> Unit,
    onBattery: () -> Unit,
    onUnpair: () -> Unit,
    version: String,
) {
    var confirm by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Orb(if (connected) OrbMode.Online else OrbMode.Offline, 72.dp)
            Column(Modifier.padding(start = 12.dp)) {
                Text(if (pairing == null) "On this phone" else if (connected) "Connected to your computer" else "Computer not reachable", style = MaterialTheme.typography.titleLarge)
                Text(if (pairing == null) "Reminders and calls work here. Connect for AI answers." else if (connected) "Live connection" else detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Section("Ask before acting") {
            SwitchRow("Confirm before calling", if (confirmCalls) "ULTRON shows the number and waits for your tap on Call." else "ULTRON calls by itself after a 3-second countdown you can cancel. Names are only called when exactly one contact matches.", confirmCalls, onConfirmCalls)
            if (!confirmCalls && !canAutoDial) Text("Allow Phone calls and Contacts below, or it will still ask.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            SwitchRow("Pick a time when it is vague", if (guessTimes) "“Remind me tomorrow” is set for 9:00 and “at 5” for the next 5 o'clock, without asking." else "ULTRON asks what you meant when a reminder time is vague.", guessTimes, onGuessTimes)
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            SwitchRow("Read answers aloud after I speak", if (speakReplies) "When you use the microphone, ULTRON answers out loud." else "ULTRON answers in text only.", speakReplies, onSpeakReplies)
        }
        Section("Hands-free") {
            SwitchRow(
                "Say “$wakeName” to wake it",
                if (handsFree) "Listening for its name now (see the notification). Try “$wakeName, play some jazz”, “$wakeName, open Spotify”, “$wakeName, pause”, “$wakeName, call Asha”."
                else "Off. When on, the microphone listens for the name while the phone is in your pocket. Android shows a notification and its microphone indicator the whole time; you can stop it there.",
                handsFree, onHandsFree,
            )
            if (handsFree) {
                androidx.compose.runtime.DisposableEffect(Unit) {
                    com.ultron.assistant.service.HandsFreeStatus.showHeard = true
                    onDispose { com.ultron.assistant.service.HandsFreeStatus.showHeard = false; com.ultron.assistant.service.HandsFreeStatus.heard = "" }
                }
                Text(com.ultron.assistant.service.HandsFreeStatus.text, style = MaterialTheme.typography.titleSmall)
                val heard = com.ultron.assistant.service.HandsFreeStatus.heard
                Text(
                    if (heard.isEmpty()) "Speak and what it understood appears here, so you can see why it did or did not wake." else "It just heard: “$heard”",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            SwitchRow("Chime when it hears the name", if (soundOnWake) "A short rising sound means “I heard you, speak now”." else "No sound; the phone gives a tiny buzz instead.", soundOnWake, onSoundOnWake)
            OutlinedButton(onClick = onPlaySound) { Text("Play the chime") }
            if (musicApps.isNotEmpty()) {
                Text("Music app for “play …”", style = MaterialTheme.typography.labelLarge)
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    androidx.compose.material3.FilterChip(selected = musicApp.isEmpty(), onClick = { onMusicApp("") }, label = { Text("Automatic") })
                    musicApps.forEach { (label, pkg) ->
                        androidx.compose.material3.FilterChip(selected = musicApp == pkg, onClick = { onMusicApp(pkg) }, label = { Text(label) })
                    }
                }
            }
            SwitchRow(
                "Understand names and songs better",
                if (onlineCommands) "After you say the name, your command is heard by Google's speech recogniser (better with accents, contact names and song titles). Only that command's audio is sent; the listening for the name stays on the phone."
                else "Off: everything is understood on the phone. Small offline recognisers often mishear song titles and names; turn this on if “play …” or “call …” gets them wrong.",
                onlineCommands, onOnlineCommands,
            )
            SwitchRow("Say the name to interrupt", if (bargeIn) "Say “$wakeName” while it is talking and it stops and listens. Uses the phone's echo cancelling." else "It finishes speaking before it listens again.", bargeIn, onBargeIn)
            SwitchRow(
                "Hear the name over music",
                if (wakeOverMusic) "While music is playing, “$wakeName” is picked out of whatever is heard; the music pauses, you speak after the chime, and it carries on afterwards. A song or video that says “$wakeName” can set it off."
                else "While music is playing, the name is only heard in a gap in the sound.",
                wakeOverMusic, onWakeOverMusic,
            )
            if (hasIndianModel) {
                Text("Offline speech model", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    androidx.compose.material3.FilterChip(selected = speechModel != "in", onClick = { onSpeechModel("us") }, label = { Text("US English") })
                    androidx.compose.material3.FilterChip(selected = speechModel == "in", onClick = { onSpeechModel("in") }, label = { Text("Indian English") })
                }
            }
            OutlinedButton(onClick = onTrain) { Text(if (trainedCount > 0) "Train my voice again ($trainedCount learned)" else "Train my voice") }
            Text("Wake name", style = MaterialTheme.typography.labelLarge)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                com.ultron.assistant.core.WakeWord.CHOICES.forEach { n ->
                    androidx.compose.material3.FilterChip(selected = n.equals(wakeName, true), onClick = { onWakeName(n) }, label = { Text(n) })
                }
            }
            Text("These names are in the offline listener's vocabulary, so they are recognised reliably. “Ultron” is not, which is why it kept being missed.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (!overlayAllowed) {
                Text("To let voice commands open apps by themselves, allow “Display over other apps”. Without it, a notification asks for one tap.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedButton(onClick = onOverlay) { Text("Allow opening apps") }
            }
        }
        Section("Your computer") {
            if (pairing == null) {
                Text("Not connected. Reminders and calls do not need it; AI answers do.", style = MaterialTheme.typography.bodyMedium)
                Button(onClick = onConnect, modifier = Modifier.height(48.dp)) { Text("Connect to your computer") }
            } else {
                Text(pairing.baseUrl, style = MaterialTheme.typography.bodyMedium.copy(fontFamily = PlexMono))
                Text(
                    if (pairing.fingerprint != null) "Encrypted. Only the certificate ${Fingerprint.pretty(pairing.fingerprint).take(23)}… is trusted." else "Not encrypted (debug build only).",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                SwitchRow("Keep a connection open in the background", "Needed for notifications from your computer. Android shows a small notification while it runs.", keepConnected, onKeepConnected)
                OutlinedButton(onClick = onBattery) { Text("Battery settings") }
            }
        }
        Section("Permissions") {
            permissions.forEachIndexed { i, p ->
                if (i > 0) HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.title, style = MaterialTheme.typography.bodyLarge)
                        Text(p.why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (p.granted) Text("Allowed", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelLarge)
                    else OutlinedButton(onClick = p.onAllow) { Text("Allow") }
                }
            }
        }
        if (pairing != null) Section("This phone") {
            Text("Also remove it on your computer (Phone panel) to cut it off completely.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedButton(onClick = { confirm = true }, colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Disconnect from this computer") }
        }
        Text("ULTRON $version", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(8.dp))
    }
    if (confirm) AlertDialog(
        onDismissRequest = { confirm = false },
        title = { Text("Disconnect from this computer?") },
        text = { Text("The phone forgets the computer and stops the background connection. Reminders and calls keep working. You can connect again at any time.") },
        confirmButton = { Button(onClick = { confirm = false; onUnpair() }) { Text("Disconnect") } },
        dismissButton = { TextButton(onClick = { confirm = false }) { Text("Cancel") } },
    )
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) { content() }
        }
    }
}

@Composable
private fun SwitchRow(title: String, body: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
