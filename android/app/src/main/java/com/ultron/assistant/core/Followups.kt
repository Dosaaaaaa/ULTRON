package com.ultron.assistant.core

/** A spoken yes or no. The offline recogniser writes "yeah", "yep", "nope" as readily as "yes" and "no". */
object Answer {
    private val yes = setOf("yes", "yeah", "yep", "yup", "yea", "sure", "okay", "ok", "correct", "right", "please", "go ahead", "do it", "go on", "that's right", "thats right", "confirm", "affirmative")
    private val no = setOf("no", "nope", "nah", "cancel", "stop", "don't", "dont", "never mind", "nevermind", "wrong", "negative", "not that one", "the next one", "next")

    private val yesFirst = setOf("yes", "yeah", "yep", "yup", "yea", "sure", "okay", "ok", "correct", "right", "confirm")
    private val noFirst = setOf("no", "nope", "nah", "cancel", "negative", "don't", "dont", "wrong")

    fun yesNo(text: String): Boolean? {
        val t = text.lowercase().replace(Regex("[^a-z' ]"), " ").replace(Regex("\\s+"), " ").trim()
        if (t.isEmpty()) return null
        if (t in yes) return true
        if (t in no) return false
        val words = t.split(' ')
        if (words.size > 4) return null // a long sentence is a new request, not an answer
        return when (words.first()) {
            in noFirst -> false
            in yesFirst -> true
            else -> null
        }
    }
}

/** What ULTRON asked, so that the next thing said (with no wake name) is taken as the answer. */
sealed interface Pending {
    /** The sentence so far; the answer is added to it and the whole parsed again ("remind me to call mom" + "in ten minutes"). */
    data class Retry(val sofar: String, val tries: Int = 0) : Pending

    /** "Did you mean [candidates[index]]?" for a call or a text. [learn] remembers how the name was heard on a "yes". */
    data class WhichContact(val heard: String, val candidates: List<Contact>, val index: Int, val text: String?, val learn: Boolean) : Pending

    /** "Call [contact]? Say yes." (calls are set to need confirmation). */
    data class ConfirmCall(val contact: Contact) : Pending
}

/** The one question that may be waiting for an answer. It lapses, so an old question never swallows a new command. */
object Followups {
    const val LIFETIME_MILLIS = 25_000L
    private var pending: Pending? = null
    private var at = 0L

    @Synchronized fun ask(p: Pending, now: Long = System.currentTimeMillis()) { pending = p; at = now }

    @Synchronized fun take(now: Long = System.currentTimeMillis()): Pending? {
        val p = pending
        pending = null
        return if (p != null && now - at in 0..LIFETIME_MILLIS) p else null
    }

    @Synchronized fun clear() { pending = null }
}

/** How a name was heard, so the same mishearing means the same contact next time. */
object ContactAliases {
    fun key(heard: String) = heard.lowercase().filter { it.isLetter() || it == ' ' }.trim().replace(Regex("\\s+"), " ")
}
