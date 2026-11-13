package com.ultron.assistant.core

/** A call proposal is only good for a few minutes: "call Asha" said at 9:00 must not be confirmed
 *  and dialled at noon. Nothing is dialled without the operator's tap on the confirmation screen. */
object CallPolicy {
    const val MAX_AGE_MILLIS = 5 * 60 * 1000L

    fun isFresh(createdAtMillis: Long?, nowMillis: Long): Boolean =
        createdAtMillis != null && nowMillis - createdAtMillis in -30_000L..MAX_AGE_MILLIS

    private val digits = Regex("[^0-9+]")

    /** A phone number safe to hand to the dialer: digits and a leading +, nothing else. */
    fun dialable(number: String): String? {
        val cleaned = number.replace(digits, "")
        val plus = cleaned.startsWith("+")
        val body = cleaned.removePrefix("+").replace("+", "")
        return if (body.length in 5..15) (if (plus) "+" else "") + body else null
    }
}

data class Contact(val name: String, val number: String)

object ContactChoice {
    /** Contacts for a spoken name. [exact] is false when nothing matched by spelling and these are only
     *  names that sound like it (a speech recogniser writes "Aasha" or "A shah" for "Asha"): such a match is
     *  never dialled without the operator confirming it. */
    data class Found(val contacts: List<Contact>, val exact: Boolean)

    /** Best matches for a spoken name, strongest first: exact, then starts-with, then contains.
     *  The phone's own contact list is searched; names never leave the phone. */
    fun rank(spoken: String, all: List<Contact>): List<Contact> = find(spoken, all).contacts

    /** Words people use for the same relative; a contact saved as "Amma" answers to "call mom". */
    private val kin = listOf(
        setOf("mom", "mum", "mummy", "mommy", "mother", "mama", "mamma", "ma", "maa", "amma", "ammi", "ammu", "mum mum"),
        setOf("dad", "daddy", "father", "papa", "pa", "appa", "abba", "baba", "achan", "acha", "pappa"),
        setOf("brother", "bro", "bhai", "anna", "annan", "ettan", "chetta", "bhaiya"),
        setOf("sister", "sis", "didi", "akka", "chechi", "chechy", "behen"),
        setOf("grandma", "granny", "grandmother", "nani", "paati", "ammamma", "ammachi", "valiyamma"),
        setOf("grandpa", "grandfather", "grandad", "thatha", "nana", "appooppan", "appachan"),
    )

    private fun kinOf(word: String): Set<String>? = kin.firstOrNull { word in it }

    fun find(spoken: String, all: List<Contact>): Found {
        val q = spoken.trim().lowercase()
        if (q.isEmpty()) return Found(emptyList(), true)
        val family = kinOf(q)
        fun score(c: Contact): Int {
            val n = c.name.lowercase()
            if (family != null && n.split(' ', '-', '_').any { it in family }) return 3
            return when {
                n == q -> 3
                n.startsWith(q) || n.split(' ').any { it == q } -> 2
                n.contains(q) -> 1
                else -> 0
            }
        }
        val scored = all.map { it to score(it) }.filter { it.second > 0 }
        val best = scored.maxOfOrNull { it.second }
        if (best != null) return Found(unique(scored.filter { it.second == best }.map { it.first }), true)
        return Found(unique(all.filter { sounds(q, it.name.lowercase()) }), false)
    }

    /** Contacts that might be who the operator meant, best first, for asking "did you mean ...?". Used when
     *  [find] found nothing by spelling. Letters and a rough consonant skeleton are both compared, because a
     *  recogniser writes an unfamiliar name as whatever English words sound closest ("Asha" -> "a share"). */
    fun suggest(spoken: String, all: List<Contact>, limit: Int = 3): List<Contact> {
        val heard = spoken.lowercase().filter { it.isLetter() }
        if (heard.length < 2) return emptyList()
        val heardKey = skeleton(spoken)
        val scored = all.mapNotNull { c ->
            val parts = c.name.lowercase().split(' ').map { w -> w.filter { it.isLetter() } }.filter { it.length >= 2 } + c.name.lowercase().filter { it.isLetter() }
            val best = parts.maxOfOrNull { w -> 0.5 * WakeTraining.similarity(heard, w) + 0.5 * WakeTraining.similarity(heardKey, skeleton(w)) } ?: return@mapNotNull null
            if (best >= 0.55) c to best else null
        }
        return unique(scored.sortedByDescending { it.second }.map { it.first }).take(limit)
    }

    /** Consonant skeleton: "asha", "a share" and "shy" all reduce to something starting with S. */
    fun skeleton(text: String): String {
        var w = text.lowercase().filter { it.isLetter() }
        for ((from, to) in listOf("sh" to "S", "ch" to "S", "zh" to "S", "th" to "t", "ph" to "f", "ck" to "k", "kn" to "n", "wr" to "r", "wh" to "w", "gh" to "", "q" to "k", "x" to "ks", "z" to "s", "w" to "v")) w = w.replace(from, to)
        w = w.replace(Regex("c(?=[eiy])"), "s").replace("c", "k")
        w = w.filter { it !in "aeiouyh" }
        return w.replace(Regex("(.)\\1+"), "$1")
    }

    private fun unique(l: List<Contact>) = l.distinctBy { CallPolicy.dialable(it.number) ?: it.number }

    /** Close in spelling or in sound to the whole name or to one word of it. */
    private fun sounds(q: String, name: String): Boolean {
        val flatQ = q.filter { it.isLetter() }
        if (flatQ.length < 3) return false
        val parts = name.split(' ').map { w -> w.filter { it.isLetter() } }.filter { it.length >= 3 } + name.filter { it.isLetter() }
        return parts.any { w -> WakeTraining.similarity(flatQ, w) >= 0.75 || soundex(flatQ) == soundex(w) }
    }

    /** Classic Soundex: names that sound alike share a code (Asha / Aasha / Ashah). */
    fun soundex(word: String): String {
        val w = word.lowercase().filter { it in 'a'..'z' }
        if (w.isEmpty()) return ""
        val codes = "01230120022455012623010202" // a..z
        val out = StringBuilder().append(w[0].uppercaseChar())
        var last = codes[w[0] - 'a']
        for (ch in w.drop(1)) {
            val c = codes[ch - 'a']
            if (c != '0' && c != last) out.append(c)
            if (ch != 'h' && ch != 'w') last = c
            if (out.length == 4) break
        }
        return out.toString().padEnd(4, '0')
    }
}
