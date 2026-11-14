package com.ultron.assistant.core

/** Short, fixed commands that cannot be the start of a longer one. Hands-free mode acts on them as soon as the
 *  heard words stop changing, instead of waiting for the recogniser's end-of-sentence silence.
 *  "play" is left out on purpose: it may be the start of "play some jazz". */
object QuickCommands {
    /** A quick command runs once the words are a whole quick command AND the microphone has been quiet this long
     *  (in 100 ms chunks). Waiting for the words alone to stop changing is not safe: the recogniser writes words
     *  late, so "stop" can sit alone for a moment inside "stop listening" (measured in docs/PHASE_5_RESULTS.md). */
    const val SETTLE_CHUNKS = 3

    fun parse(text: String): AppCommand? {
        if (text.isBlank()) return null
        return when (val c = AppCommands.parse(Spoken.fix(text))) {
            is AppCommand.Media -> if (c.key == MediaKey.PLAY) null else c
            is AppCommand.Volume -> c
            else -> null
        }
    }

    /** The action itself is the answer (the music stops, the volume bar shows), so nothing is spoken. */
    fun silent(c: AppCommand) = c is AppCommand.Volume || (c is AppCommand.Media && c.key != MediaKey.PLAY)

    /** True when [text], as it was finally recognised, is the command that already ran on the partial words. */
    fun sameAs(fired: AppCommand, text: String) = parse(text) == fired
}
