package com.ultron.assistant.service

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.ultron.assistant.BuildConfig
import com.ultron.assistant.R
import com.ultron.assistant.core.AppCommand
import com.ultron.assistant.core.Followups
import com.ultron.assistant.core.QuickCommands
import com.ultron.assistant.core.WakeWord
import com.ultron.assistant.data.Store
import com.ultron.assistant.ui.Voice

/** Hands-free mode: say the wake name (and a command) with the phone untouched. OFF until the operator
 *  turns it on in the app. While it runs, Android shows a notification with a Stop button and its
 *  microphone indicator.
 *
 *  Listening is done by an offline recogniser bundled in the app (Vosk): continuous, so there is no
 *  system "tick" and no gap between rounds, and nothing leaves the phone. Everything that does not start
 *  with the wake name is dropped on the spot; nothing is stored. A command is only acted on if it
 *  followed the name, in the same breath or in the next utterance within a few seconds.
 *
 *  While music is playing there is no silence, so sentences never end: then (and while ULTRON itself is
 *  talking) the name counts wherever it turns up in the newest words. Hearing it takes the audio focus,
 *  which makes music apps pause for the command and the answer, and gives it back afterwards.
 *
 *  Limits: the small offline model is less accurate than Google's on unusual words (song titles,
 *  names); continuous listening costs battery; Android 14+ only lets it start from the app screen,
 *  so it does not restart itself after a reboot. */
class HandsFreeService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var listener: VoskListener
    private lateinit var voice: Voice
    private lateinit var router: VoiceRouter
    private var wake = WakeWord.DEFAULT.lowercase()
    private var aliases: List<String> = emptyList()
    private val shown get() = wake.replaceFirstChar { it.uppercase() }
    private var awaitingCommand = false
    private var running = false
    private var busy = false // a command is being carried out or answered aloud
    private var turn = 0 // bumped for every command and every interruption; an answer for an older turn is dropped
    private var listeningOnline = false // Google's recogniser has the microphone for one command
    private lateinit var focus: Focus
    private var seen = 0 // words of the running text already looked at for the name (music / reply mode)
    private var saysName = false // the reply being spoken contains the wake name: it must not interrupt itself

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { Store(this).handsFree = false; stopSelf(); return START_NOT_STICKY }
        if (BuildConfig.DEBUG && running && intent?.action == ACTION_DEBUG_HEAR) {
            // Debug builds only (see DebugCommandReceiver): feeds text in as if the recogniser had heard it.
            val text = intent.getStringExtra("text").orEmpty()
            if (intent.getBooleanExtra("settled", false)) onSettled(text)
            else if (intent.getBooleanExtra("final", false)) onHeard(text) else onPartial(text)
            return START_NOT_STICKY
        }
        Notifier.channels(this)
        wake = Store(this).wakeName.lowercase()
        aliases = Store(this).wakeAliases(wake)
        HandsFreeStatus.set(false, "Starting…")
        ServiceCompat.startForeground(this, Notifier.HANDS_FREE_ID, note("Say “$shown” and a command. Tap Stop to turn this off."), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            fail("Microphone permission is off."); return START_NOT_STICKY
        }
        if (!running) {
            running = true
            voice = Voice(this)
            focus = Focus(this)
            router = VoiceRouter(this) { later -> main.post { if (running && !busy && !awaitingCommand) { busy = true; focus.hold(); speakAndFinish(later, false, turn) } } }
            listener = VoskListener(this)
            listener.load(
                indian = Store(this).speechModel == "in",
                onReady = { HandsFreeStatus.set(true, "Listening for “$shown”"); listener.start(::onHeard, { fail(it) }, ::onPartial, Store(this).bargeIn, ::onSettled) },
                onFail = { fail(it) },
            )
        }
        return START_NOT_STICKY
    }

    private fun note(text: String) = NotificationCompat.Builder(this, Notifier.STATUS_CHANNEL).setSmallIcon(R.drawable.ic_ultron)
        .setContentTitle("ULTRON is listening for its name").setContentText(text).setOngoing(true).setSilent(true)
        .addAction(0, "Stop", PendingIntent.getService(this, 1, Intent(this, HandsFreeService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE))
        .build()

    private fun fail(why: String) {
        HandsFreeStatus.set(false, "Stopped: $why")
        Store(this).handsFree = false
        if (Notifier.canPost(this)) {
            getSystemService(NotificationManager::class.java).notify(
                Notifier.HANDS_FREE_ID + 100,
                NotificationCompat.Builder(this, Notifier.ALERT_CHANNEL).setSmallIcon(R.drawable.ic_ultron)
                    .setContentTitle("Hands-free stopped").setContentText(why).setAutoCancel(true).build(),
            )
        }
        main.post { stopSelf() }
    }

    /** Words as they are being recognised. The moment the name is the only thing said, so far, and nothing
     *  more follows for a beat, the chime plays: the operator does not have to wait for the end-of-sentence
     *  pause to learn that it heard them. While ULTRON is talking, the name stops it. Over music the name
     *  alone is enough: the music pauses, the chime plays, and the command comes after it. */
    private fun onPartial(partial: String) = main.post {
        if (!running || listeningOnline) return@post
        val from = newWords(partial)
        if (busy) {
            if (Store(this).bargeIn && !saysName && WakeWord.late(partial, wake, aliases, from)) interrupt()
            return@post
        }
        if (awaitingCommand) return@post
        if (HandsFreeStatus.showHeard) HandsFreeStatus.heard = partial
        main.removeCallbacks(nameOnly)
        if (overMusic()) {
            if (WakeWord.late(partial, wake, aliases, from)) cueListening()
            return@post
        }
        val after = WakeWord.match(partial, wake, aliases)
        if (after != null && after.isBlank()) main.postDelayed(nameOnly, 450) // else: a command is following, wait for it
    }

    // ---- quick commands: "pause", "next", "louder" run when the words are exactly that AND the speaker has gone quiet,
    // not after the recogniser's longer end-of-sentence pause (measured: 0 wrong fires in 36 clips, 0.5-0.8 s sooner).
    private var firedQuick: AppCommand? = null
    private var firedAt = 0L

    private fun onSettled(words: String) = main.post {
        if (!running || busy || listeningOnline || !Store(this).quickCommands) return@post
        val said = if (awaitingCommand) words else WakeWord.match(words, wake, aliases) ?: return@post
        val cmd = QuickCommands.parse(said) ?: return@post
        firedQuick = cmd; firedAt = SystemClock.uptimeMillis()
        if (BuildConfig.DEBUG) android.util.Log.i("UltronDebug", "quick command ran: $cmd")
        val wasAwaiting = awaitingCommand
        main.removeCallbacks(nameOnly); main.removeCallbacks(giveUp)
        awaitingCommand = false
        listener.setPatient(false)
        listener.reset() // the rest of this sentence is not wanted: no second result for it
        runCommand(said, wasAwaiting)
    }

    /** Where the words that were not in the last running text begin (one back: the last word is often rewritten). */
    private fun newWords(text: String): Int {
        val from = maxOf(0, seen - 1)
        seen = WakeWord.count(text)
        return if (from < seen) from else 0 // shorter than before: a new sentence began
    }

    private fun overMusic() = Store(this).wakeOverMusic && focus.musicPlaying

    private val nameOnly = Runnable { if (running && !awaitingCommand && !busy) cueListening() }

    /** The operator said the name over ULTRON's voice (or while it was working): stop, and listen. */
    private fun interrupt() {
        turn++
        busy = false
        voice.stopSpeaking()
        Followups.clear()
        saysName = false
        main.removeCallbacks(nameOnly)
        HandsFreeStatus.text = "Listening for “$shown” · stopped, listening"
        cueListening()
    }

    /** "Yes, I am listening": the chime (or a buzz when sounds are off), then the next utterance is the command. */
    private fun cueListening() {
        awaitingCommand = true
        seen = 0
        focus.hold() // music pauses here, so the command is heard in quiet
        listener.setPatient(true)
        main.removeCallbacks(giveUp)
        main.postDelayed(giveUp, if (Store(this).onlineCommands) 16000 else 9000)
        HandsFreeStatus.text = "Listening for “$shown” · heard you, waiting for your command"
        val store = Store(this)
        val next = { if (running) { if (store.onlineCommands && voice.googleAvailable()) listenOnline() else listener.pause(false) } }
        if (store.soundOnWake) {
            // Do not listen while it plays (it would hear itself); resetting also drops the half-heard name.
            listener.pause(true)
            Chime.play(Chime.Kind.READY) { main.post(next) }
        } else {
            getSystemService(Vibrator::class.java)?.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
            listener.pause(true); main.post(next)
        }
    }

    /** One command heard by Google's recogniser (better with accents, names, songs). The offline one lets go of the
     *  microphone meanwhile; if Google fails or hears nothing, the offline one carries on. */
    private fun listenOnline() {
        listeningOnline = true
        listener.releaseMic()
        HandsFreeStatus.text = "Listening for “$shown” · listening for your command"
        val myTurn = turn
        voice.listen(
            onPartial = { p -> main.post { if (HandsFreeStatus.showHeard) HandsFreeStatus.heard = p } },
            onText = { t -> main.post { backOffline(); if (running && myTurn == turn) handleHeard(t) } },
            onError = { _, _ -> main.post { backOffline() } },
            forceGoogle = true, // this switch means Google's recogniser, whatever the microphone button uses
        )
    }

    private fun backOffline() {
        if (!listeningOnline) return
        listeningOnline = false
        if (running) { listener.resumeMic(); listener.pause(false) }
    }

    /** One finished utterance from a recogniser (runs on the main thread). */
    private fun onHeard(heard: String) { main.post { handleHeard(heard) } }

    private fun handleHeard(heard: String) {
        if (!running) return
        if (HandsFreeStatus.showHeard) HandsFreeStatus.heard = heard
        main.removeCallbacks(nameOnly)
        firedQuick?.let { fired ->
            firedQuick = null
            if (SystemClock.uptimeMillis() - firedAt < 3000 && QuickCommands.sameAs(fired, WakeWord.match(heard, wake, aliases) ?: heard)) {
                if (BuildConfig.DEBUG) android.util.Log.i("UltronDebug", "final result of the quick command dropped")
                return
            }
        }
        val from = newWords(heard)
        seen = 0 // a sentence ended
        var named = WakeWord.match(heard, wake, aliases)
        if (busy) {
            if (Store(this).bargeIn && !saysName) {
                if (named != null) {
                    interrupt()
                    if (named.isNotBlank()) { awaitingCommand = false; main.removeCallbacks(giveUp); runCommand(named, false) }
                } else if (WakeWord.late(heard, wake, aliases, from)) interrupt()
            }
            return
        }
        // Over music the name may sit anywhere in what was heard, and what surrounds it is as likely the song
        // as a command: the name alone counts, and the command is asked for after the chime, in quiet.
        if (!awaitingCommand && overMusic() && (named != null || WakeWord.late(heard, wake, aliases, from))) named = ""
        if (awaitingCommand && named != null && named.isBlank()) return // the name again, or the tail of the one that just chimed
        val command = if (awaitingCommand) heard else named
        main.removeCallbacks(giveUp)
        val wasAwaiting = awaitingCommand
        awaitingCommand = false
        listener.setPatient(false)
        when {
            command == null -> Unit // not addressed to ULTRON: dropped here
            command.isBlank() -> cueListening() // only the name, and the partial path did not get there first
            else -> runCommand(command, wasAwaiting)
        }
    }

    private fun runCommand(command: String, wasAwaiting: Boolean) {
        if (!wasAwaiting && Store(this).soundOnWake) Chime.play(Chime.Kind.GOT_IT) // name and command in one breath
        HandsFreeStatus.text = "Listening for “$shown” · last command ${java.text.DateFormat.getTimeInstance(java.text.DateFormat.SHORT).format(java.util.Date())}"
        busy = true
        focus.hold()
        val myTurn = ++turn
        // With "speak over it" on, the microphone stays open (echo-cancelled) so the name can interrupt the reply.
        if (!Store(this).bargeIn) listener.pause(true)
        router.handle(command) { r -> main.post { if (myTurn == turn) speakAndFinish(r.text, r.stop, myTurn, r.listenAgain, r.quiet, r.silent) } }
    }

    /** [quiet]: the operator asked for the music to stop, so it must not come back when the turn ends. */
    private fun speakAndFinish(text: String, stop: Boolean, forTurn: Int, listenAgain: Boolean = false, quiet: Boolean = false, silent: Boolean = false) {
        if (stop) { Store(this).handsFree = false; voice.speak(text) { stopSelf() }; return }
        val finish = {
            if (forTurn == turn) {
                busy = false
                saysName = false
                if (running) {
                    listener.reset()
                    seen = 0
                    if (!Store(this).bargeIn) listener.pause(false)
                    if (listenAgain) cueListening() else if (quiet) focus.releaseQuiet() else focus.release()
                }
            }
        }
        if (silent) { finish(); return } // the action was the answer: nothing to say
        saysName = WakeWord.late(text, wake, aliases)
        voice.speak(text) { finish() }
    }

    /** Nothing was said after the chime: stop waiting and let the music carry on. */
    private val giveUp = Runnable {
        awaitingCommand = false
        listener.setPatient(false)
        if (listeningOnline) { voice.cancel(); backOffline() }
        if (!busy) focus.release()
    }

    override fun onDestroy() {
        if (!HandsFreeStatus.text.startsWith("Stopped")) HandsFreeStatus.set(false, "Off")
        HandsFreeStatus.active = false
        running = false
        main.removeCallbacksAndMessages(null)
        if (this::listener.isInitialized) listener.stop()
        if (this::focus.isInitialized) focus.release()
        if (this::voice.isInitialized) voice.shutdown()
        if (this::router.isInitialized) router.shutdown()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.ultron.assistant.HANDS_FREE_STOP"
        const val ACTION_DEBUG_HEAR = "com.ultron.assistant.HANDS_FREE_DEBUG_HEAR"
        fun start(context: Context) { context.startForegroundService(Intent(context, HandsFreeService::class.java)) }
        /** Stop listening without turning the setting off (voice training needs the microphone). */
        fun suspend(context: Context) { context.stopService(Intent(context, HandsFreeService::class.java)) }
        /** Start listening again with new settings (name, model, echo cancelling) without turning the setting off. */
        fun restart(context: Context) {
            suspend(context)
            Handler(Looper.getMainLooper()).postDelayed({ if (Store(context).handsFree) start(context) }, 600)
        }
        fun stop(context: Context) { context.startService(Intent(context, HandsFreeService::class.java).setAction(ACTION_STOP)) }
    }
}
