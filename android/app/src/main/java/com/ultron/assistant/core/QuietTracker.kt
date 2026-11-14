package com.ultron.assistant.core

import kotlin.math.max

/** Tells, from the loudness of each 100 ms of microphone audio, how long the speaker has been quiet since they last
 *  spoke. The speech level follows the speaker (fast up, slowly down); "quiet" is well below it. Noise that is close
 *  to the speech level is never quiet, and a room with no speech at all never counts, so the failure direction is
 *  safe: nothing fires early, the recogniser's own end-of-sentence result still comes. */
class QuietTracker {
    private var level = 0f
    var quietChunks = 0
        private set

    fun feed(rms: Float) {
        level = max(rms, level * DECAY)
        quietChunks = if (level >= FLOOR && rms < level * QUIET_RATIO) quietChunks + 1 else 0
    }

    /** True once the speaker has been quiet for [chunks] chunks in a row (each chunk is 100 ms). */
    fun settled(chunks: Int) = quietChunks >= chunks

    fun reset() { level = 0f; quietChunks = 0 }

    private companion object {
        const val DECAY = 0.95f // per 100 ms chunk: the level halves in about 1.4 s
        const val FLOOR = 500f // 16-bit sample RMS below which nothing was said
        const val QUIET_RATIO = 0.25f
    }
}
