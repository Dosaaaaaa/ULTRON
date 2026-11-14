package com.ultron.assistant.core

/** Finds the wake name at the start of what the speech recogniser heard (hands-free mode).
 *  Only the first words count, so mentioning the name mid-sentence does not wake the phone.
 *
 *  The offline recogniser (Vosk, small English model) only writes words it knows. "Ultron" is not one
 *  of them, so it is never heard correctly; [CHOICES] are names that are in its vocabulary. */
object WakeWord {
    /** Names measured to work with the offline recogniser (see docs/PHASE_5_RESULTS.md, "Phone 1.7.0"). */
    val CHOICES = listOf("Mercury", "Sentinel", "Friday", "Jarvis", "Computer", "Nova", "Orion", "Athena")
    const val DEFAULT = "Mercury"

    /** App 1.7.0 changed the default from "Friday" (too well known, and weak in noise) to "Mercury". Returns the name
     *  to store once, for a phone still on the old default or with none chosen; null when nothing should change. */
    fun migrate(stored: String?, alreadyDone: Boolean): String? =
        if (alreadyDone) null else if (stored == null || stored.equals("Friday", ignoreCase = true)) DEFAULT else null

    private val lead = setOf("hey", "hi", "hello", "okay", "ok", "yo", "oh")
    private val ultron = Regex("^[uao][aeiou]?l+t+r[aeiouh]{0,3}n$")
    private val punct = Regex("[^\\p{L}\\p{N}\\s']")

    /** The words after the name ("" if only the name was said), or null when the name was not said. */
    fun match(text: String, name: String = "ultron", aliases: List<String> = emptyList()): String? {
        val wake = name.lowercase().trim()
        val learned = learned(aliases)
        val words = words(text)
        for (start in 0..minOf(2, words.size - 1)) {
            if (start > 0 && words[start - 1] !in lead) break
            nameAt(words, start, wake, learned)?.let { return words.drop(it).joinToString(" ") }
        }
        return null
    }

    /** For when music or ULTRON's own voice is playing: there is no silence, so a sentence never ends and
     *  the name turns up in the middle of the running text. True if the name is among the words from
     *  [from] on (the ones that were not there last time). Measured in docs/PHASE_5_RESULTS.md. */
    fun late(text: String, name: String, aliases: List<String> = emptyList(), from: Int = 0): Boolean {
        val wake = name.lowercase().trim()
        val learned = learned(aliases)
        val words = words(text)
        return (maxOf(0, from) until words.size).any { nameAt(words, it, wake, learned) != null }
    }

    /** How many words [text] has, counted the way [late] counts them. */
    fun count(text: String): Int = words(text).size

    private fun words(text: String) = punct.replace(text.lowercase(), " ").split(Regex("\\s+")).filter { it.isNotEmpty() }

    private fun learned(aliases: List<String>) = aliases.map { it.lowercase().filter { c -> c.isLetterOrDigit() } }.toSet()

    /** The index just after the name if it starts at [start] (learned spellings may span up to three words). */
    private fun nameAt(words: List<String>, start: Int, wake: String, learned: Set<String>): Int? {
        for (span in 1..3) {
            if (start + span > words.size) continue
            val heard = words.subList(start, start + span).joinToString("")
            val hit = if (wake == "ultron") ultron.matches(heard) && span <= 2
            else (span == 1 && heard == wake) || heard in learned
            if (hit) return start + span
        }
        return null
    }
}
