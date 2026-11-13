package com.ultron.assistant.service

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.StorageService

/** Offline, continuous speech-to-text for hands-free mode. The model ships inside the app; audio is
 *  decoded on the phone, never sent anywhere, and no recogniser start/stop means no system beep.
 *
 *  The microphone is read here (not by Vosk's own helper) for three reasons: it can be opened as a
 *  "voice communication" source, which has the phone's echo canceller, so ULTRON's own voice and music
 *  are largely removed and the operator can talk over it; it can be let go while another recogniser uses
 *  the microphone; and the end-of-sentence pause can be made longer while a command is being spoken. */
class VoskListener(private val context: Context) {
    private var model: Model? = null
    private var recognizer: Recognizer? = null
    private var thread: Thread? = null
    private var onText: (String) -> Unit = {}
    private var onFail: (String) -> Unit = {}
    private var onPartial: (String) -> Unit = {}
    private var echoCancel = true

    @Volatile private var running = false
    @Volatile private var paused = false
    @Volatile private var resetRequested = false
    @Volatile private var patient = false
    @Volatile private var appliedPatient = false

    /** Unpacks (first run only) and loads the bundled model off the main thread. [indian] picks the Indian English model if it is bundled. */
    fun load(indian: Boolean, onReady: () -> Unit, onFail: (String) -> Unit) {
        val bundled = runCatching { context.assets.list("model-en-in")?.isNotEmpty() == true }.getOrDefault(false)
        val (asset, target) = if (indian && bundled) "model-en-in" to "vosk-model-in" else "model-en-us" to "vosk-model"
        StorageService.unpack(context, asset, target, { m -> model = m; onReady() }, { e -> onFail(e.message ?: "model failed to load") })
    }

    /** Calls [onText] with every finished utterance (a pause ends one) and [onPartial] with words so far. */
    fun start(onText: (String) -> Unit, onFail: (String) -> Unit, onPartial: (String) -> Unit = {}, echoCancel: Boolean = true) {
        val m = model ?: return onFail("model not loaded")
        this.onText = onText; this.onFail = onFail; this.onPartial = onPartial; this.echoCancel = echoCancel
        recognizer = Recognizer(m, 16000f)
        openMic()
    }

    @SuppressLint("MissingPermission") // checked by the service before it starts
    private fun openMic() {
        if (running) return
        running = true
        thread = Thread({ loop() }, "vosk-mic").also { it.start() }
    }

    private fun loop() {
        val rec = recognizer ?: return
        val size = maxOf(AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 8000)
        val ar = openRecord(size) ?: run { running = false; onFail("microphone could not be opened"); return }
        val effects = if (echoCancel) attachEffects(ar.audioSessionId) else emptyList()
        try {
            ar.startRecording()
            val buf = ShortArray(1600) // 100 ms
            var last = ""
            while (running) {
                val n = ar.read(buf, 0, buf.size)
                if (n < 0) { onFail("microphone error"); break }
                if (n == 0 || paused) continue
                if (resetRequested) { resetRequested = false; rec.reset(); last = "" }
                if (patient != appliedPatient) { appliedPatient = patient; rec.setEndpointerMode(if (patient) 2 else 0) }
                if (rec.acceptWaveForm(buf, n)) {
                    last = ""
                    text(rec.result)?.let(onText)
                } else {
                    val p = partial(rec.partialResult)
                    if (p != null && p != last) { last = p; onPartial(p) }
                }
            }
        } catch (e: RuntimeException) {
            onFail(e.message ?: "microphone error")
        } finally {
            runCatching { ar.stop() }
            effects.forEach { runCatching { it.release() } }
            ar.release()
        }
    }

    /** Voice-communication first (echo cancelling), plain voice recognition if the phone refuses it. */
    @SuppressLint("MissingPermission")
    private fun openRecord(size: Int): AudioRecord? {
        val sources = if (echoCancel) listOf(MediaRecorder.AudioSource.VOICE_COMMUNICATION, MediaRecorder.AudioSource.VOICE_RECOGNITION) else listOf(MediaRecorder.AudioSource.VOICE_RECOGNITION)
        for (src in sources) {
            val ar = runCatching { AudioRecord(src, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size * 2) }.getOrNull() ?: continue
            if (ar.state == AudioRecord.STATE_INITIALIZED) return ar
            ar.release()
        }
        return null
    }

    private fun attachEffects(session: Int) = buildList {
        if (AcousticEchoCanceler.isAvailable()) runCatching { AcousticEchoCanceler.create(session)?.also { it.enabled = true } }.getOrNull()?.let { add(it) }
        if (NoiseSuppressor.isAvailable()) runCatching { NoiseSuppressor.create(session)?.also { it.enabled = true } }.getOrNull()?.let { add(it) }
    }

    private fun partial(json: String?): String? =
        runCatching { JSONObject(json ?: return null).optString("partial") }.getOrNull()?.takeIf { it.isNotBlank() }

    private fun text(json: String?): String? =
        runCatching { JSONObject(json ?: return null).optString("text") }.getOrNull()?.takeIf { it.isNotBlank() }

    /** Ignore the microphone while [paused] (a chime is playing); coming back drops whatever was half heard. */
    fun pause(paused: Boolean) {
        this.paused = paused
        if (!paused) resetRequested = true
    }

    /** Forget a half-heard sentence (the reply that was just spoken must not become a command). */
    fun reset() { resetRequested = true }

    /** A longer pause is allowed inside a sentence while a command is expected ("remind me ... to drink water"). */
    fun setPatient(on: Boolean) { patient = on }

    /** Let go of the microphone so another recogniser can use it. */
    fun releaseMic() {
        running = false
        thread?.let { runCatching { it.join(1500) } }
        thread = null
    }

    /** Take the microphone back after [releaseMic]. */
    fun resumeMic() {
        resetRequested = true
        openMic()
    }

    fun stop() {
        releaseMic()
        recognizer?.close(); recognizer = null
        model?.close(); model = null
    }
}
