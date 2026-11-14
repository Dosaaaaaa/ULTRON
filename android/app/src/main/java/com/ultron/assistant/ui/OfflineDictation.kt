package com.ultron.assistant.ui

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.ultron.assistant.core.Dictation
import com.ultron.assistant.core.SpeechErrors
import com.ultron.assistant.service.VoskListener

/** The microphone button without Google: the same offline recogniser the wake-name listener uses, run only while
 *  the operator is talking. Nothing leaves the phone. The model stays loaded between taps. */
class OfflineDictation(context: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val appContext = context.applicationContext
    private val listener = VoskListener(appContext)
    private var token = 0
    private var last = ""
    private var onText: (String) -> Unit = {}
    private var onError: (String) -> Unit = {}
    private var active = false

    private val silence = Runnable { if (active) finish(null) }

    fun available(): Boolean = runCatching { appContext.assets.list("model-en-us")?.isNotEmpty() == true }.getOrDefault(false)

    fun listen(indian: Boolean, onPartial: (String) -> Unit, onText: (String) -> Unit, onError: (String) -> Unit) {
        cancel()
        val mine = ++token
        this.onText = onText; this.onError = onError
        last = ""; active = true
        val begin = {
            if (mine == token && active) {
                listener.start(
                    onText = { t -> main.post { if (mine == token && active) finish(t) } },
                    onFail = { why -> main.post { if (mine == token && active) { stop(); this.onError("The microphone did not start ($why).") } } },
                    onPartial = { p -> main.post { if (mine == token && active) { last = p; onPartial(p) } } },
                    echoCancel = false,
                )
                main.postDelayed(silence, MAX_WAIT_MS)
            }
        }
        if (listener.loaded) begin()
        else listener.load(indian, onReady = { main.post(begin) }, onFail = { why -> main.post { if (mine == token && active) { stop(); this.onError("The offline listener could not load ($why).") } } })
    }

    /** The button was tapped again: use what was heard so far. */
    fun release() { if (active) finish(null) }

    fun cancel() { token++; stop() }

    private fun finish(finalText: String?) {
        val text = Dictation.pick(finalText, last)
        val deliver = onText; val fail = onError
        stop()
        if (text == null) fail(SpeechErrors.message(SpeechErrors.NO_MATCH)) else deliver(text)
    }

    private fun stop() {
        active = false
        main.removeCallbacks(silence)
        listener.idle()
    }

    fun shutdown() { token++; stop(); listener.stop() }

    private companion object { const val MAX_WAIT_MS = 15000L }
}
