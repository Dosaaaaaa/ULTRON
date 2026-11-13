package com.ultron.assistant.service

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.Looper

/** Audio focus for one hands-free turn. Taking it asks other apps to pause their music while ULTRON
 *  listens and answers (so the command is heard in quiet); letting go lets the music come back.
 *  Whether an app pauses and resumes is up to that app; music players generally do. */
class Focus(context: Context) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    private var held: AudioFocusRequest? = null

    private fun request(gain: Int, onChange: (Int) -> Unit = {}) =
        AudioFocusRequest.Builder(gain).setAudioAttributes(attributes).setOnAudioFocusChangeListener({ onChange(it) }, main).build()

    /** True while other apps' music is playing (ULTRON's own voice counts too, so ask before speaking). */
    val musicPlaying: Boolean get() = audio.isMusicActive

    /** Other apps pause until [release]. Safe to call again while it is already held. */
    fun hold() {
        if (held != null) return
        // A music app that starts playing takes the focus for good: nothing is held any more.
        val r = request(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT) { if (it == AudioManager.AUDIOFOCUS_LOSS) held = null }
        if (audio.requestAudioFocus(r) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) held = r
    }

    /** The turn is over: whatever was paused may carry on. */
    fun release() {
        held?.let { audio.abandonAudioFocusRequest(it) }
        held = null
    }

    /** The operator asked for the music to stop: it must not start again when the turn ends. Taking the
     *  focus "for good" for a moment tells the paused app that it has lost it, not just lent it. */
    fun releaseQuiet() {
        val r = request(AudioManager.AUDIOFOCUS_GAIN)
        val granted = audio.requestAudioFocus(r) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        release() // after the request above, so the paused app is never told "carry on" in between
        if (granted) main.postDelayed({ audio.abandonAudioFocusRequest(r) }, 600)
    }
}
