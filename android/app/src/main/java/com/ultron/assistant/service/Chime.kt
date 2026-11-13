package com.ultron.assistant.service

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlin.math.PI
import kotlin.math.sin

/** The short rising chime that says "I heard my name, speak now". Generated in code (no sound file),
 *  played as an assistant sound so it follows the phone's normal volume. */
object Chime {
    private const val RATE = 22050

    enum class Kind { READY, GOT_IT }

    /** 16-bit mono samples. READY = two rising notes; GOT_IT = one short softer note. */
    fun samples(kind: Kind): ShortArray {
        val notes = when (kind) {
            Kind.READY -> listOf(784.0 to 0.09, 1175.0 to 0.12)
            Kind.GOT_IT -> listOf(988.0 to 0.07)
        }
        val amp = if (kind == Kind.READY) 0.40 else 0.28
        val out = ArrayList<Short>()
        for ((hz, seconds) in notes) {
            val n = (RATE * seconds).toInt()
            val fade = (RATE * 0.012).toInt() // 12 ms in and out, so there is no click
            for (i in 0 until n) {
                val env = minOf(1.0, i / fade.toDouble(), (n - 1 - i) / fade.toDouble())
                out += (sin(2 * PI * hz * i / RATE) * env * amp * Short.MAX_VALUE).toInt().toShort()
            }
        }
        return out.toShortArray()
    }

    fun durationMillis(kind: Kind): Long = samples(kind).size * 1000L / RATE

    /** Plays [kind] and calls [done] after it has finished (on a background thread). Never throws. */
    fun play(kind: Kind, done: () -> Unit = {}) {
        Thread {
            try {
                val pcm = samples(kind)
                val track = AudioTrack.Builder()
                    .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                    .setBufferSizeInBytes(pcm.size * 2)
                    .setTransferMode(AudioTrack.MODE_STATIC)
                    .build()
                track.write(pcm, 0, pcm.size)
                track.play()
                Thread.sleep(durationMillis(kind) + 60)
                track.release()
            } catch (_: Exception) {
                // no sound is better than a crash in the listening service
            } finally {
                done()
            }
        }.start()
    }
}
