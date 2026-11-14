package com.ultron.assistant.core

import org.json.JSONArray
import org.json.JSONObject

/** The kinds of notification ULTRON may read. Everything else is ignored. */
enum class Source(val key: String, val label: String, val plural: String) {
    SMS("sms", "text message", "text messages"),
    WHATSAPP("whatsapp", "WhatsApp message", "WhatsApp messages"),
    EMAIL("email", "email", "emails"),
    TEAMS("teams", "Teams message", "Teams messages");

    companion object { fun byKey(key: String) = entries.firstOrNull { it.key == key } }
}

/** One kept notification: who, what (a short preview) and when. Created only from a [Verdict.Keep]. */
data class Note(val source: Source, val sender: String, val preview: String, val whenMs: Long)

sealed interface Verdict {
    /** Not one of the listed apps (or empty): not looked at. */
    data object Ignore : Verdict
    /** A listed app, but the content is a code, a bank or payment message or a business sender: never stored. */
    data class Drop(val why: String) : Verdict
    data class Keep(val source: Source) : Verdict
}

/** What may be read, and what must never be. Runs on the phone before anything is stored; when unsure it drops. */
object NoteFilter {
    private val apps = mapOf(
        "com.google.android.apps.messaging" to Source.SMS, "com.android.mms" to Source.SMS, "com.android.messaging" to Source.SMS,
        "com.samsung.android.messaging" to Source.SMS, "com.motorola.messaging" to Source.SMS,
        "com.whatsapp" to Source.WHATSAPP, "com.whatsapp.w4b" to Source.WHATSAPP,
        "com.google.android.gm" to Source.EMAIL, "com.google.android.gm.lite" to Source.EMAIL,
        "com.microsoft.teams" to Source.TEAMS,
    )

    /** Banking, payment, authenticator and password apps: never read, whatever else is configured. */
    private val protectedApps = listOf(
        "phonepe", "paytm", "paisa", "bhim", "upi", "bank", "wallet", "gpay", "authenticator", "authy", "otp", "bitwarden",
        "1password", "onepassword", "keepass", "lastpass", "dashlane", "keeper", "password", "hdfc", "icici", "sbi", "axis",
        "kotak", "yesbank", "idfc", "pnb", "mobikwik", "freecharge", "razorpay", "payment",
    )

    private val sensitive = listOf(
        "sensitive (?:notification )?content hidden", "content hidden",
        "\\botp\\b", "one[- ]?time", "verification", "verify your", "security code", "authenticat", "log-?in code", "sign[- ]?in code",
        "pass ?codes?", "\\b2fa\\b", "two[- ]factor", "\\bcvv\\b", "\\bcvc\\b", "\\bmpin\\b", "\\bpin\\b", "password",
        "do(?: not|n't|nt) share", "never share",
        "debited", "credited", "withdrawn", "\\ba/c\\b", "\\bacct\\b", "account (?:no|number|ending|balance)", "avl\\.? ?bal",
        "avail(?:able)? bal", "\\bupi\\b", "\\bneft\\b", "\\bimps\\b", "\\brtgs\\b", "\\bifsc\\b", "card ending", "credit card", "debit card",
        "\\btxn\\b", "transaction (?:id|of|ref)", "net ?banking", "\\bemi\\b",
    ).map { Regex(it, RegexOption.IGNORE_CASE) }
    private val codeWord = Regex("\\bcode\\b", RegexOption.IGNORE_CASE)
    private val codeDigits = Regex("\\b\\d{4,8}\\b|\\b\\d{3}[- ]\\d{3}\\b|\\b\\d{2} \\d{2} \\d{2}\\b")
    private val senderId = Regex("^[A-Z]{2}-[A-Z0-9]{3,10}(?:-[A-Z])?$|^[A-Z]{6,11}$")

    fun source(pkg: String): Source? = apps[pkg]

    fun neverRead(pkg: String): Boolean = pkg.lowercase().let { p -> protectedApps.any { it in p } }

    /** True when the text looks like a one-time code, a security warning or a bank/payment message. */
    fun isSensitive(text: String): Boolean = sensitive.any { it.containsMatchIn(text) } || (codeWord.containsMatchIn(text) && codeDigits.containsMatchIn(text))

    /** [extra] maps more packages to a kind (debug builds only, for emulator tests). */
    fun judge(pkg: String, title: String, text: String, extra: Map<String, Source> = emptyMap()): Verdict {
        if (neverRead(pkg)) return Verdict.Ignore
        val source = apps[pkg] ?: extra[pkg] ?: return Verdict.Ignore
        if (title.isBlank() && text.isBlank()) return Verdict.Ignore
        if (isSensitive("$title\n$text")) return Verdict.Drop("code, bank or payment text")
        if (source == Source.SMS && senderId.matches(title.trim())) return Verdict.Drop("business sender")
        return Verdict.Keep(source)
    }
}

/** The kept notes: pruning, de-duplication, the cap and the on-phone text form. */
object NoteLog {
    const val KEEP_MS = 48L * 3_600_000
    const val MAX = 200
    const val PREVIEW_MAX = 160
    private const val DUPLICATE_MS = 10 * 60_000L

    fun prune(list: List<Note>, now: Long) = list.filter { it.whenMs >= now - KEEP_MS }

    fun add(list: List<Note>, n: Note): List<Note> {
        if (list.any { it.source == n.source && it.sender == n.sender && it.preview == n.preview && kotlin.math.abs(n.whenMs - it.whenMs) < DUPLICATE_MS }) return list
        return (list + n).takeLast(MAX)
    }

    fun unseen(list: List<Note>, seenAt: Long) = list.filter { it.whenMs > seenAt }

    /** The sender as the app shows it, without a trailing "(3 messages)". */
    fun sender(title: String): String = clip(title.replace(Regex("\\s*\\(\\d+ (?:new )?messages?\\)\\s*$"), ""))

    fun clip(text: String): String = text.replace(Regex("\\s+"), " ").trim().take(PREVIEW_MAX)

    fun encode(list: List<Note>): String = JSONArray().also { a ->
        list.forEach { a.put(JSONObject().put("s", it.source.key).put("w", it.sender).put("p", it.preview).put("t", it.whenMs)) }
    }.toString()

    fun decode(json: String): List<Note> = runCatching {
        val a = JSONArray(json)
        (0 until a.length()).mapNotNull { i ->
            val o = a.getJSONObject(i)
            Note(Source.byKey(o.getString("s")) ?: return@mapNotNull null, o.getString("w"), o.getString("p"), o.getLong("t"))
        }
    }.getOrDefault(emptyList())
}

/** What ULTRON says about the kept notes. Built by plain code: counts, senders and short previews. */
object Digest {
    private const val READ_MAX = 5

    private fun glue(items: List<String>): String = items.withIndex().joinToString("") { (i, s) ->
        if (i == items.lastIndex) s else if (s.last() in ".?!") "$s " else "$s. "
    }

    private fun names(senders: List<String>): String = when {
        senders.size == 1 -> senders[0]
        senders.size == 2 -> "${senders[0]} and ${senders[1]}"
        senders.size == 3 -> "${senders[0]}, ${senders[1]} and ${senders[2]}"
        else -> "${senders[0]}, ${senders[1]}, ${senders[2]} and ${senders.size - 3} ${if (senders.size - 3 == 1) "other" else "others"}"
    }

    private fun part(source: Source, notes: List<Note>): String {
        val n = notes.size
        return "$n ${if (n == 1) source.label else source.plural} from ${names(notes.map { it.sender }.distinct())}"
    }

    private fun previews(notes: List<Note>): List<String> {
        val shown = notes.takeLast(READ_MAX)
        return shown.map { "${it.sender}: ${it.preview}" } + if (notes.size > shown.size) listOf("And ${notes.size - shown.size} more earlier") else emptyList()
    }

    fun missed(notes: List<Note>): String {
        val parts = Source.entries.mapNotNull { s -> notes.filter { it.source == s }.takeIf { it.isNotEmpty() }?.let { part(s, it) } }
        return when (parts.size) {
            0 -> "Nothing new."
            1 -> "You missed ${parts[0]}."
            else -> "You missed ${parts.dropLast(1).joinToString(", ")} and ${parts.last()}."
        }
    }

    fun messages(notes: List<Note>): String {
        val m = notes.filter { it.source == Source.SMS || it.source == Source.WHATSAPP }
        if (m.isEmpty()) return "No new messages."
        return glue(listOf("You have ${m.size} ${if (m.size == 1) "message" else "messages"}") + previews(m))
    }

    fun about(source: Source, notes: List<Note>): String {
        val m = notes.filter { it.source == source }
        if (m.isEmpty()) return "No new ${source.plural}."
        return glue(listOf("You have ${m.size} ${if (m.size == 1) source.label else source.plural}") + previews(m))
    }

    fun team(notes: List<Note>): String {
        val m = notes.filter { it.source == Source.TEAMS }
        val caveat = "I can only see Teams notifications, not your team's status."
        if (m.isEmpty()) return "No new Teams messages. $caveat"
        return glue(listOf("You have ${m.size} new ${if (m.size == 1) Source.TEAMS.label else Source.TEAMS.plural}") + previews(m) + caveat)
    }

    fun from(name: String, notes: List<Note>): String {
        val m = notes.filter { it.sender.contains(name.trim(), ignoreCase = true) }
        if (m.isEmpty()) return "Nothing from ${name.trim()}."
        val shown = m.takeLast(READ_MAX)
        return glue(shown.map { "${it.sender} says: ${it.preview}" } + if (m.size > shown.size) listOf("And ${m.size - shown.size} more earlier") else emptyList())
    }

    fun morning(notes: List<Note>, hour: Int): String {
        val hello = if (hour < 12) "Good morning." else if (hour < 17) "Hello." else "Good evening."
        return "$hello " + if (notes.isEmpty()) (if (hour < 12) "Nothing new overnight." else "Nothing new.") else missed(notes)
    }
}

sealed interface ReadCommand {
    data object Missed : ReadCommand
    data object Messages : ReadCommand
    data object Emails : ReadCommand
    data object Team : ReadCommand
    data class From(val name: String) : ReadCommand
    data object Morning : ReadCommand
    data object Clear : ReadCommand
}

/** The spoken ways of asking what came in. Matched by code; the sentence must be exactly such a request. */
object ReadCommands {
    private const val FILLER = "(?:(?:please|hey|ok|okay|can you|could you|would you|tell me|just)[\\s,:]+)*"
    private fun rx(body: String) = Regex("^\\s*$FILLER(?:$body)\\s*[.?!]*\\s*$", RegexOption.IGNORE_CASE)

    private val notPeople = setOf("i", "you", "we", "they", "he", "she", "it", "someone", "anyone", "everyone")
    private const val LEAD = "(?:(?:do i have|have i got|have i received|did i get|did i receive|are there|any)\\s+)+(?:new\\s+)?"

    private val fromA = rx("(?:read|show|tell)(?:\\s+me)?\\s+(?:the\\s+|my\\s+)?(?:new\\s+)?(?:messages?|texts?|whatsapps?)\\s+from\\s+(.{1,40}?)")
    private val fromB = rx("${LEAD}(?:messages?|texts?|whatsapps?)\\s+from\\s+(.{1,40}?)")
    private val fromC = rx("what\\s+did\\s+(.{1,40}?)\\s+(?:say|text|send|write|message)(?:\\s+me)?")
    private val team = rx("what(?:'s| is)\\s+(?:my\\s+)?team(?:'s|s)?\\s+(?:status|update)|(?:any|new)\\s+teams?\\s+(?:messages?|notifications?|updates?)|what(?:'s| is)\\s+happening\\s+(?:on|in)\\s+teams|check\\s+teams")
    private val emails = rx("${LEAD}(?:e-?mails?|mail)|(?:check|read)(?:\\s+(?:my|the))?(?:\\s+new)?\\s+(?:e-?mails?|mail)")
    private val messages = rx("${LEAD}(?:text\\s+|whatsapp\\s+)?(?:messages?|texts?|whatsapps?)|(?:has|did|have)\\s+(?:anyone|anybody|someone|somebody)\\s+(?:texted|messaged|text|message|whatsapped)(?:\\s+me)?|who\\s+(?:texted|messaged)\\s+me|read(?:\\s+me)?(?:\\s+(?:my|the))?(?:\\s+new)?\\s+(?:messages|texts|whatsapps)")
    private val morning = rx("good morning|(?:(?:what(?:'s| is)|give me|tell me)\\s+the\\s+|my\\s+|the\\s+)?(?:morning\\s+)?summary|morning summary")
    private val missed = rx("what did i miss|what have i missed|what(?:'s| is) new|anything new|catch me up|any notifications|did i miss (?:anything|something)")
    private val clear = rx("(?:clear|forget|delete|dismiss)\\s+(?:all\\s+)?(?:my\\s+|the\\s+)?(?:notifications?|messages?|notes)")

    fun parse(text: String): ReadCommand? {
        for (r in listOf(fromA, fromB, fromC)) {
            val name = r.find(text)?.groupValues?.get(1)?.trim()?.trimEnd('.', '?', '!') ?: continue
            if (name.isNotEmpty() && name.lowercase() !in notPeople) return ReadCommand.From(name)
        }
        return when {
            team.matches(text) -> ReadCommand.Team
            emails.matches(text) -> ReadCommand.Emails
            messages.matches(text) -> ReadCommand.Messages
            morning.matches(text) -> ReadCommand.Morning
            missed.matches(text) -> ReadCommand.Missed
            clear.matches(text) -> ReadCommand.Clear
            else -> null
        }
    }
}

/** What to say for a [ReadCommand], and which kinds were thereby told (so they are not repeated next time). */
object Briefing {
    data class Answer(val text: String, val told: Set<Source>)

    private fun fresh(all: List<Note>, seen: Map<Source, Long>, kinds: Set<Source>) =
        all.filter { it.source in kinds && it.whenMs > (seen[it.source] ?: 0L) }

    fun answer(cmd: ReadCommand, all: List<Note>, seen: Map<Source, Long>, hour: Int): Answer = when (cmd) {
        ReadCommand.Missed -> fresh(all, seen, Source.entries.toSet()).let { Answer(Digest.missed(it), Source.entries.toSet()) }
        ReadCommand.Morning -> fresh(all, seen, Source.entries.toSet()).let { Answer(Digest.morning(it, hour), Source.entries.toSet()) }
        ReadCommand.Messages -> setOf(Source.SMS, Source.WHATSAPP).let { Answer(Digest.messages(fresh(all, seen, it)), it) }
        ReadCommand.Emails -> setOf(Source.EMAIL).let { Answer(Digest.about(Source.EMAIL, fresh(all, seen, it)), it) }
        ReadCommand.Team -> setOf(Source.TEAMS).let { Answer(Digest.team(fresh(all, seen, it)), it) }
        is ReadCommand.From -> Answer(Digest.from(cmd.name, all), emptySet())
        ReadCommand.Clear -> Answer("Cleared everything I had read.", emptySet())
    }
}

/** When the morning summary is read by itself: the Clock app's alarm notification goes away (the alarm was dismissed)
 *  in the morning hours, once a day. Whether a given phone's Clock app behaves this way has to be seen on that phone. */
object MorningTrigger {
    fun shouldFire(enabled: Boolean, pkg: String, category: String?, hour: Int, today: String, doneDay: String): Boolean =
        enabled && category == "alarm" && "clock" in pkg.lowercase() && hour in 4..11 && today != doneDay
}
