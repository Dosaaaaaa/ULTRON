package com.ultron.assistant.core

/** Voice training: the person says the wake name a few times and the offline recogniser's transcripts
 *  show how it actually hears them. Variants it consistently writes instead of the name ("fried a",
 *  "freda") are remembered for this person, so the name works for their voice and accent.
 *
 *  Only close, rare-looking variants are kept: everyday words are refused so training can never make
 *  the phone wake on ordinary speech. */
object WakeTraining {
    data class Result(val hits: Int, val total: Int, val learned: List<String>, val all: List<String>)

    private const val MAX_ALIASES = 12
    private val ordinary = setOf(
        "the", "a", "an", "i", "it", "to", "and", "of", "in", "is", "you", "that", "he", "was", "for", "on", "are", "with", "as",
        "his", "they", "be", "at", "one", "have", "this", "from", "or", "had", "by", "but", "what", "some", "we", "can", "out",
        "other", "were", "all", "there", "when", "up", "use", "your", "how", "said", "each", "she", "which", "do", "their", "time",
        "if", "will", "way", "about", "many", "then", "them", "would", "like", "so", "these", "her", "make", "see", "him", "two",
        "has", "look", "more", "day", "could", "go", "come", "did", "no", "yes", "my", "me", "okay", "ok", "hey", "hello", "hi",
        "yeah", "well", "oh", "um", "uh", "now", "just", "not", "please", "stop", "play", "pause", "next", "open", "call",
    )
    private val lead = setOf("hey", "hi", "hello", "okay", "ok", "yo", "oh")
    private val punct = Regex("[^\\p{L}\\p{N}\\s']")

    fun words(text: String): List<String> = punct.replace(text.lowercase(), " ").split(Regex("\\s+")).filter { it.isNotEmpty() }

    /** 1.0 = identical, 0.0 = nothing in common (edit distance over the longer length). */
    fun similarity(a: String, b: String): Double {
        if (a == b) return 1.0
        val d = Array(a.length + 1) { IntArray(b.length + 1) }
        for (i in 0..a.length) d[i][0] = i
        for (j in 0..b.length) d[0][j] = j
        for (i in 1..a.length) for (j in 1..b.length) {
            d[i][j] = minOf(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
        }
        return 1.0 - d[a.length][b.length].toDouble() / maxOf(a.length, b.length)
    }

    /** [heard] = what the recogniser wrote for each attempt at saying [name] (blank attempts omitted). */
    fun learn(name: String, heard: List<String>, existing: List<String> = emptyList()): Result {
        val wake = name.lowercase()
        var hits = 0
        val misses = ArrayList<String>()
        for (t in heard) {
            if (WakeWord.match(t, wake, existing) != null) { hits++; continue }
            val w = words(t).dropWhile { it in lead }
            if (w.size in 1..2) misses += w.joinToString(" ")
        }
        val counts = misses.groupingBy { it }.eachCount()
        val learned = counts.filter { (cand, n) ->
            val flat = cand.filter { it.isLetterOrDigit() }
            flat.length >= 3 && flat != wake && (' ' in cand || cand !in ordinary) &&
                similarity(flat, wake) >= (if (n >= 2) 0.4 else 0.5)
        }.keys.toList()
        val all = (existing + learned).distinct().takeLast(MAX_ALIASES)
        return Result(hits, heard.size, learned.filter { it in all }, all)
    }
}
