package com.ultron.assistant.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Column
import kotlinx.coroutines.launch
import java.time.Instant
import com.ultron.assistant.core.Display
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.Scaffold
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material.icons.filled.Add
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.ultron.assistant.BuildConfig
import com.ultron.assistant.service.LinkStatus
import com.ultron.assistant.service.HandsFreeService
import com.ultron.assistant.service.PushService

class MainActivity : ComponentActivity() {
    private val vm: UltronViewModel by viewModels()
    private lateinit var voice: Voice

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        edgeToEdge()
        voice = Voice(this)
        handleIntent(intent)
        setContent { UltronTheme { Root() } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** `ultron://pair?...` (a scanned QR code opened by the camera app): only offered, never applied. */
    private fun handleIntent(intent: Intent?) {
        val data = intent?.data ?: return
        if (intent.action == Intent.ACTION_VIEW && data.scheme == "ultron") vm.offerLink(data.toString())
    }

    /** The switch records what the operator asked for; make it true. An app update or a system kill stops the
     *  listening service without turning the setting off, which looked like "on" but heard nothing. */
    override fun onResume() {
        super.onResume()
        if (vm.store.handsFree && !com.ultron.assistant.service.HandsFreeStatus.active && !training && has(Manifest.permission.RECORD_AUDIO)) {
            HandsFreeService.start(this)
        }
    }

    private var training by mutableStateOf(false)

    override fun onDestroy() {
        voice.shutdown()
        super.onDestroy()
    }

    private fun scan() {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { b -> b.rawValue?.let(vm::offerLink) }
            .addOnFailureListener { vm.pairError = "Scanning did not work (${it.message ?: "cancelled"}). You can enter the details by hand." }
    }

    private fun has(permission: String) = ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    @Composable
    private fun Root() {
        val pairing = vm.pairing
        val askNotifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            // The service starts before the answer, so its first status could not be shown; restart it.
            if (granted && vm.store.load() != null) PushService.start(this@MainActivity)
        }
        // Notifications are needed for reminders, so ask for them straight away (not only after pairing).
        LaunchedEffect(Unit) {
            if (Build.VERSION.SDK_INT >= 33 && !has(Manifest.permission.POST_NOTIFICATIONS)) {
                askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        vm.pendingLink?.let { link ->
            ConfirmLinkDialog(link.baseUrl, link.fingerprint, onConfirm = { vm.confirmLink(Build.MODEL ?: "Phone") }, onCancel = vm::cancelLink)
        }
        if (training) {
            val n = vm.store.wakeName
            TrainScreen(
                n, vm.store.wakeAliases(n),
                onSave = { vm.store.setWakeAliases(n, it) },
                onClose = { training = false; if (vm.store.handsFree) HandsFreeService.start(this@MainActivity) },
            )
        } else if (vm.showPair || vm.pendingLink != null) {
            PairScreen(vm.pairBusy, vm.pairError, Build.MODEL ?: "Phone", onScan = ::scan, onManual = vm::pairManually, onBack = { vm.showPair = false })
        } else {
            Main(pairing) // the app works without a computer; connecting is optional
        }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    private fun Main(pairing: com.ultron.assistant.data.Pairing?) {
        var tab by rememberSaveable { mutableIntStateOf(0) }
        var addingReminder by remember { mutableStateOf(false) }
        val link by LinkStatus.state.collectAsStateWithLifecycle()
        val snackbar = remember { SnackbarHostState() }
        val scope = rememberCoroutineScope()
        // The speech output for voice replies; typed chat stays silent.
        val speak: (String) -> Unit = { voice.speak(it) }
        LaunchedEffect(tab) { if (tab == 1) vm.refreshPhoneReminders() }
        Scaffold(
            containerColor = androidx.compose.ui.graphics.Color.Transparent,
            contentWindowInsets = WindowInsets(0, 0, 0, 0), // the theme already keeps clear of the system bars
            snackbarHost = { SnackbarHost(snackbar) },
            floatingActionButton = {
                if (tab == 1) {
                    ExtendedFloatingActionButton(onClick = { addingReminder = true }, icon = { Icon(Icons.Filled.Add, null) }, text = { Text("New reminder") })
                }
            },
            bottomBar = {
                NavigationBar {
                    NavigationBarItem(tab == 0, { tab = 0 }, { Icon(Icons.Filled.Home, null) }, label = { Text("Chat") })
                    NavigationBarItem(tab == 1, { tab = 1 }, { Icon(Icons.Filled.Notifications, null) }, label = { Text("Reminders") })
                    NavigationBarItem(tab == 2, { tab = 2 }, { Icon(Icons.Filled.Settings, null) }, label = { Text("Settings") })
                }
            },
        ) { padding ->
            Box(Modifier.padding(padding)) {
                when (tab) {
                    0 -> ChatScreen(
                        lines = vm.lines, busy = vm.chatBusy, note = vm.chatNote, canRetry = vm.canRetry, listening = vm.listening,
                        voiceAvailable = voice.available(), partial = vm.partial,
                        canFixMic = vm.micFixPackage != null, onFixMic = ::openMicFix,
                        onSend = { vm.send(it, false, speak) }, onRetry = { vm.retry(speak) },
                        onMicClick = ::toggleListening,
                        onNewChat = vm::newChat,
                        status = if (pairing == null) "Reminders and calls work on this phone" else if (link.connected) "Connected to your computer" else "Computer not reachable",
                    )
                    1 -> RemindersScreen(
                        vm.phoneReminders, false, null, addingReminder, { addingReminder = false },
                        vm::refreshPhoneReminders,
                        { text, due -> vm.addPhoneReminder(text, due); scope.launch { snackbar.showSnackbar("Reminder added") } },
                        { id ->
                            vm.removePhoneReminder(id) { item ->
                                scope.launch {
                                    val undo = snackbar.showSnackbar("Reminder removed", actionLabel = "Undo", duration = SnackbarDuration.Long)
                                    if (undo == SnackbarResult.ActionPerformed) {
                                        Display.parseInstant(item.dueAt)?.takeIf { it.isAfter(Instant.now()) }?.let { vm.addPhoneReminder(item.text, it) }
                                    }
                                }
                            }
                        },
                    )
                    else -> SettingsTab(pairing, link.connected, link.detail)
                }
            }
        }
    }

    private val askMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startListening() else vm.chatNote = "Allow the microphone to talk to ULTRON (Settings > Permissions)."
    }

    /** Opens the system's page for the app whose missing microphone permission blocks speech recognition. */
    private fun openMicFix() {
        val pkg = vm.micFixPackage ?: return
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, android.net.Uri.fromParts("package", pkg, null)))
    }

    /** Tap once to start, speak, and it stops by itself when you pause; tap again to stop sooner. */
    private fun toggleListening() {
        if (vm.listening) { voice.release(); return }
        if (!has(Manifest.permission.RECORD_AUDIO)) { askMic.launch(Manifest.permission.RECORD_AUDIO); return }
        startListening()
    }

    private fun startListening() {
        vm.chatNote = null
        vm.micFixPackage = null
        vm.partial = ""
        vm.listening = true
        voice.listen(
            onPartial = { vm.partial = it },
            onText = { text -> vm.listening = false; vm.partial = ""; vm.send(text, true) { voice.speak(it) } },
            onError = { msg, fix -> vm.listening = false; vm.partial = ""; vm.chatNote = msg; vm.micFixPackage = fix },
        )
    }

    @Composable
    private fun SettingsTab(pairing: com.ultron.assistant.data.Pairing?, connected: Boolean, detail: String) {
        var keep by remember { mutableStateOf(vm.store.keepConnected) }
        var tick by remember { mutableIntStateOf(0) } // re-read permissions when coming back from system settings
        val owner = LocalLifecycleOwner.current
        DisposableEffect(owner) {
            val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) tick++ }
            owner.lifecycle.addObserver(obs)
            onDispose { owner.lifecycle.removeObserver(obs) }
        }
        val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++; if (it) PushService.start(this) }
        val askMicHere = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
        val askContacts = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
        val askCall = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
        val permissions = remember(tick) {
            listOf(
                PermissionRow("Notifications", "Reminders and call confirmations", Build.VERSION.SDK_INT < 33 || has(Manifest.permission.POST_NOTIFICATIONS)) { askNotif.launch(Manifest.permission.POST_NOTIFICATIONS) },
                PermissionRow("Microphone", "Only while you hold the talk button", has(Manifest.permission.RECORD_AUDIO)) { askMicHere.launch(Manifest.permission.RECORD_AUDIO) },
                PermissionRow("Contacts", "To find a name when you say “call Asha”. Never sent anywhere.", has(Manifest.permission.READ_CONTACTS)) { askContacts.launch(Manifest.permission.READ_CONTACTS) },
                PermissionRow("Phone calls", "To start a call after you tap Call (otherwise the dialer opens)", has(Manifest.permission.CALL_PHONE)) { askCall.launch(Manifest.permission.CALL_PHONE) },
            )
        }
        val canAutoDial = has(Manifest.permission.CALL_PHONE) && has(Manifest.permission.READ_CONTACTS)
        var handsFree by remember { mutableStateOf(vm.store.handsFree) }
        var wakeName by remember { mutableStateOf(vm.store.wakeName) }
        var soundOnWake by remember { mutableStateOf(vm.store.soundOnWake) }
        var musicApp by remember { mutableStateOf(vm.store.musicApp) }
        var onlineCommands by remember { mutableStateOf(vm.store.onlineCommands) }
        var bargeIn by remember { mutableStateOf(vm.store.bargeIn) }
        var wakeOverMusic by remember { mutableStateOf(vm.store.wakeOverMusic) }
        var speechModel by remember { mutableStateOf(vm.store.speechModel) }
        val hasIndianModel = remember { runCatching { assets.list("model-en-in")?.isNotEmpty() == true }.getOrDefault(false) }
        val restartListening = { if (handsFree) HandsFreeService.restart(this) }
        var askOverlay by remember { mutableStateOf(false) }
        val installedMusic = remember { com.ultron.assistant.core.MusicApps.KNOWN.filter { packageManager.getLaunchIntentForPackage(it.second) != null } }
        if (askOverlay) {
            androidx.compose.material3.AlertDialog(
                onDismissRequest = { askOverlay = false },
                title = { Text("Let ULTRON open apps by voice?") },
                text = { Text("Opening an app or playing music from the background needs Android's “Display over other apps” permission. Without it, each request needs one tap on a notification. ULTRON uses it only to open the app you asked for.") },
                confirmButton = { androidx.compose.material3.TextButton(onClick = { askOverlay = false; startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:$packageName"))) }) { Text("Allow") } },
                dismissButton = { androidx.compose.material3.TextButton(onClick = { askOverlay = false }) { Text("Not now") } },
            )
        }
        val askMicForHandsFree = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            tick++
            if (granted) { vm.store.handsFree = true; handsFree = true; HandsFreeService.start(this); if (!Settings.canDrawOverlays(this)) askOverlay = true }
        }
        SettingsScreen(
            pairing, connected, detail, keep, permissions,
            handsFree = handsFree, wakeName = wakeName,
            soundOnWake = soundOnWake, onSoundOnWake = { soundOnWake = it; vm.store.soundOnWake = it },
            onPlaySound = { com.ultron.assistant.service.Chime.play(com.ultron.assistant.service.Chime.Kind.READY) },
            musicApps = installedMusic, musicApp = musicApp, onMusicApp = { musicApp = it; vm.store.musicApp = it },
            trainedCount = vm.store.wakeAliases(wakeName).size,
            onTrain = {
                if (!has(Manifest.permission.RECORD_AUDIO)) askMicHere.launch(Manifest.permission.RECORD_AUDIO)
                else { HandsFreeService.suspend(this); training = true }
            },
            onWakeName = { wakeName = it; vm.store.wakeName = it; restartListening() },
            overlayAllowed = Settings.canDrawOverlays(this),
            onlineCommands = onlineCommands, onOnlineCommands = { onlineCommands = it; vm.store.onlineCommands = it },
            bargeIn = bargeIn, onBargeIn = { bargeIn = it; vm.store.bargeIn = it; restartListening() },
            wakeOverMusic = wakeOverMusic, onWakeOverMusic = { wakeOverMusic = it; vm.store.wakeOverMusic = it },
            speechModel = speechModel, hasIndianModel = hasIndianModel, onSpeechModel = { speechModel = it; vm.store.speechModel = it; restartListening() },
            onHandsFree = { on ->
                if (!on) { vm.store.handsFree = false; handsFree = false; HandsFreeService.stop(this) }
                else if (!has(Manifest.permission.RECORD_AUDIO)) askMicForHandsFree.launch(Manifest.permission.RECORD_AUDIO)
                else { vm.store.handsFree = true; handsFree = true; HandsFreeService.start(this); if (!Settings.canDrawOverlays(this)) askOverlay = true }
            },
            onOverlay = { startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:$packageName"))) },
            confirmCalls = vm.confirmCalls, guessTimes = vm.guessTimes, speakReplies = vm.speakReplies, canAutoDial = canAutoDial,
            onConfirmCalls = vm::changeConfirmCalls, onGuessTimes = vm::changeGuessTimes, onSpeakReplies = vm::changeSpeakReplies,
            onConnect = { vm.showPair = true },
            onKeepConnected = { keep = it; vm.store.keepConnected = it; if (it) PushService.start(this) else PushService.stop(this) },
            onBattery = { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) },
            onUnpair = vm::unpair,
            version = BuildConfig.VERSION_NAME,
        )
    }

}
