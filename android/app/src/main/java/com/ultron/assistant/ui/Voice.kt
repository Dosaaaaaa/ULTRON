package com.ultron.assistant.ui

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.content.pm.PackageManager
import android.provider.Settings
import com.ultron.assistant.core.RecognizerService
import com.ultron.assistant.core.ServiceChoice
import com.ultron.assistant.core.SpeechErrors
import com.ultron.assistant.core.TtsEngines
import java.util.Locale

/** Push-to-talk. The microphone is open only while the button is held, and Android shows its own
 *  indicator. Recognition is Android's SpeechRecognizer; the text goes to the chat like typed text.
 *  There is no wake word and no background listening.
 *
 *  Lifecycle (the bug this replaces): letting go of the button only ASKS the recogniser to finish
 *  (stopListening); it must stay alive until it delivers its result. The earlier version destroyed it
 *  on release, so the result never arrived and every attempt looked like a failure. */
private const val RecognitionServiceAction = "android.speech.RecognitionService"

class Voice(private val context: Context) {
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private val offline by lazy { OfflineDictation(context) }
    private var usingOffline = false
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var pendingSpeech: String? = null

    private fun google() = com.ultron.assistant.data.Store(context).googleTalk

    /** Android's own recogniser (Google's, or the phone maker's) is installed. */
    fun googleAvailable(): Boolean =
        SpeechRecognizer.isRecognitionAvailable(context) ||
            (Build.VERSION.SDK_INT >= 33 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context))

    fun available(): Boolean = (!google() && offline.available()) || googleAvailable()

    /** Start listening. [onPartial] shows words as they are recognised; exactly one of [onText] or
     *  [onError] is called at the end. */
    fun listen(onPartial: (String) -> Unit, onText: (String) -> Unit, onError: (String, String?) -> Unit, forceGoogle: Boolean = false) {
        teardown()
        // Default: the offline recogniser on the phone. Google's is used only when the operator switched it on
        // (Settings > Voice), or when the offline model is not in this build.
        usingOffline = !forceGoogle && !google() && offline.available()
        if (usingOffline) {
            offline.listen(com.ultron.assistant.data.Store(context).speechModel == "in", onPartial, onText) { onError(it, null) }
            return
        }
        start(preferOnDevice = false, onPartial, onText, onError)
    }

    /** The recognition services installed on this phone, and whether each may use the microphone. */
    private fun services(): List<RecognizerService> {
        val pm = context.packageManager
        return pm.queryIntentServices(Intent(RecognitionServiceAction), 0).mapNotNull { ri ->
            val si = ri.serviceInfo ?: return@mapNotNull null
            RecognizerService(si.packageName, si.name, pm.checkPermission(android.Manifest.permission.RECORD_AUDIO, si.packageName) == PackageManager.PERMISSION_GRANTED)
        }
    }

    private fun start(preferOnDevice: Boolean, onPartial: (String) -> Unit, onText: (String) -> Unit, onError: (String, String?) -> Unit) {
        val standard = SpeechRecognizer.isRecognitionAvailable(context)
        val onDevice = Build.VERSION.SDK_INT >= 33 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        val useOnDevice = Build.VERSION.SDK_INT >= 33 && ((preferOnDevice && onDevice) || (!standard && onDevice))
        if (!standard && !onDevice) {
            onError("This phone has no speech recognition service. Install or enable Google's voice typing.", null)
            return
        }
        // The phone's default recogniser often lacks the microphone permission itself, which makes every
        // request fail. Pick one that has it; if none does, say exactly which app to fix.
        val choice = ServiceChoice.pick(Settings.Secure.getString(context.contentResolver, "voice_recognition_service"), services())
        if (choice is ServiceChoice.Blocked && !(useOnDevice)) {
            onError(
                "Speech recognition is blocked: “${appLabel(choice.fix.packageName)}” has no microphone permission. Tap Fix permission, open Permissions, and allow Microphone.",
                choice.fix.packageName,
            )
            return
        }
        val r = when {
            useOnDevice && Build.VERSION.SDK_INT >= 33 -> SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
            choice is ServiceChoice.Use -> SpeechRecognizer.createSpeechRecognizer(context, ComponentName(choice.service.packageName, choice.service.className))
            else -> SpeechRecognizer.createSpeechRecognizer(context)
        }
        recognizer = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onPartialResults(partialResults: Bundle?) {
                partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let(onPartial)
            }

            override fun onResults(results: Bundle?) {
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                teardown()
                if (text.isNullOrBlank()) onError(SpeechErrors.message(SpeechErrors.NO_MATCH), null) else onText(text)
            }

            override fun onError(error: Int) {
                teardown()
                if (!useOnDevice && onDevice && SpeechErrors.worthTryingOnDevice(error)) {
                    start(true, onPartial, onText, onError) // the Google service failed: try the on-device one once
                } else {
                    onError(SpeechErrors.message(error), if (error == SpeechErrors.INSUFFICIENT_PERMISSIONS) services().firstOrNull()?.packageName else null)
                }
            }

            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })
        r.startListening(
            Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName),
        )
    }

    private fun appLabel(pkg: String): String = runCatching {
        val pm = context.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    }.getOrDefault(pkg)

    /** The button was released: ask the recogniser to finish and deliver. It is destroyed when it
     *  answers, or after a few seconds if it never does. */
    fun release() {
        if (usingOffline) { offline.release(); return }
        val r = recognizer ?: return
        r.stopListening()
        main.postDelayed({ if (recognizer === r) teardown() }, 6000)
    }

    /** Abandon any attempt in progress (a new one is starting, or the screen is closing). */
    fun cancel() { if (usingOffline) offline.cancel(); teardown() }

    private fun teardown() {
        val r = recognizer ?: return
        recognizer = null
        runCatching { r.cancel() }
        runCatching { r.destroy() }
    }

    /** Speak a reply. The best installed voice for the phone's language is chosen (neural/network voices
     *  sound far more natural than the default), a little slower and lower than normal. */
    fun speak(text: String, onDone: (() -> Unit)? = null) {
        doneCallback = onDone
        if (tts == null) {
            pendingSpeech = text
            tts = TextToSpeech(context.applicationContext, { status ->
                ttsReady = status == TextToSpeech.SUCCESS
                if (ttsReady) {
                    tune()
                    tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                        override fun onStart(utteranceId: String?) = Unit
                        override fun onDone(utteranceId: String?) { main.post { finishSpeech() } }
                        @Deprecated("Deprecated in Java")
                        override fun onError(utteranceId: String?) { main.post { finishSpeech() } }
                    })
                    pendingSpeech?.let { tts?.speak(it, TextToSpeech.QUEUE_FLUSH, null, "reply") }
                } else main.post { finishSpeech() }
                pendingSpeech = null
            }, TtsEngines.choose(com.ultron.assistant.data.Store(context).ttsEngine, installedEngines(context).map { it.second }))
        } else if (ttsReady) {
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "reply")
        } else main.post { finishSpeech() }
    }

    /** The engine setting changed: the next reply is spoken by the new engine. */
    fun resetSpeech() {
        runCatching { tts?.shutdown() }
        tts = null; ttsReady = false; pendingSpeech = null
    }

    /** Cut a reply off mid-sentence (the operator spoke over it). The finish callback is dropped. */
    fun stopSpeaking() {
        doneCallback = null
        pendingSpeech = null
        runCatching { tts?.stop() }
    }

    private var doneCallback: (() -> Unit)? = null
    private fun finishSpeech() { val cb = doneCallback; doneCallback = null; cb?.invoke() }

    private fun tune() {
        val t = tts ?: return
        val locale = Locale.getDefault()
        runCatching { t.language = locale }
        val best = runCatching {
            t.voices.orEmpty()
                .filter { it.locale.language == locale.language && !it.features.orEmpty().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED) }
                .maxByOrNull { v -> v.quality * 10 + (if (v.locale.country == locale.country) 5 else 0) - v.latency / 100 }
        }.getOrNull()
        if (best != null) runCatching { t.voice = best }
        t.setSpeechRate(0.95f)
        t.setPitch(0.88f)
    }

    fun shutdown() {
        if (usingOffline) offline.shutdown()
        teardown()
        main.removeCallbacksAndMessages(null)
        tts?.shutdown()
        tts = null
    }

}

/** Installed text-to-speech engines as (label, package). */
fun installedEngines(context: Context): List<Pair<String, String>> {
    val pm = context.packageManager
    return pm.queryIntentServices(Intent("android.intent.action.TTS_SERVICE"), 0).mapNotNull { ri ->
        val si = ri.serviceInfo ?: return@mapNotNull null
        (ri.loadLabel(pm)?.toString() ?: si.packageName) to si.packageName
    }.distinctBy { it.second }
}
