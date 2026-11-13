package com.ultron.assistant.core

import java.time.DayOfWeek
import java.time.ZonedDateTime

/** Commands the phone understands by itself, with no computer and no internet:
 *  "remind me in 10 minutes to stretch", "remind me tomorrow at 9 to call mum", "call Asha", "dial 555 0100". */
sealed interface Command {
    data class Remind(val text: String, val due: ZonedDateTime) : Command
    /** [sure] is false when "call" may have been misheard ("cool", "called"): such a sentence is only a call if a
     *  contact matches exactly. [loose] means the name looks like ordinary speech ("a share", "the bell"): it is a
     *  call if a contact matches or sounds like it, and otherwise it is passed on as a normal sentence. */
    data class Call(val kind: String, val target: String, val sure: Boolean = true, val loose: Boolean = false) : Command
    /** Needs more from the operator. [retry] is the sentence so far: the next thing said is added to it and parsed again. */
    data class Unclear(val why: String, val retry: String? = null) : Command
}

object Commands {
    private val prefix = Regex(
        "^\\s*(?:(?:please|ultron|hey|can you|could you|would you)[\\s,:]+)*" +
            "(?:remind me|set (?:a |an )?reminder|add (?:a )?reminder|make (?:a )?reminder)\\b[\\s,:]*(.*?)\\s*$",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )
    private val callCmd = Regex(
        "^\\s*(?:(?:please|ultron|hey|can you|could you|would you)[\\s,:]+)*" +
            "(call|called|cool|coal|cold|phone|ring|dial)\\s+(.{1,60}?)\\s*[.?!]*\\s*$",
        RegexOption.IGNORE_CASE,
    )
    private val number = Regex("^\\+?[\\d][\\d ()\\-.]{4,19}$")
    private val notATarget = setOf(
        "me", "it", "him", "her", "them", "us", "you", "this", "that", "back", "a", "an", "the", "off", "up", "out", "in", "on",
        "now", "later", "again", "home", "someone", "somebody", "anyone", "everyone", "my", "your", "our",
    )
    private val nameShape = Regex("^[\\p{L}][\\p{L}\\p{N}'’.\\-]*(?: [\\p{L}][\\p{L}\\p{N}'’.\\-]*){0,3}$")

    private val words = mapOf(
        "a" to 1, "an" to 1, "one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5, "six" to 6, "seven" to 7, "eight" to 8,
        "nine" to 9, "ten" to 10, "fifteen" to 15, "twenty" to 20, "thirty" to 30, "forty" to 40, "forty-five" to 45, "sixty" to 60,
    )
    private val num = "(\\d{1,4}|" + words.keys.sortedByDescending { it.length }.joinToString("|") + ")"
    private val relative = Regex("\\bin\\s+(?:(half an hour)|$num\\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)|(an hour))\\b", RegexOption.IGNORE_CASE)
    private val days = listOf("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    private val dayWord = Regex("\\b(today|tonight|tomorrow|" + days.joinToString("|") + ")\\b", RegexOption.IGNORE_CASE)
    private val atClock = Regex("\\bat\\s+(?:(\\d{1,2})(?:[:.](\\d{2}))?\\s*(a\\.?m\\.?|p\\.?m\\.?)?|(noon|midnight))\\b", RegexOption.IGNORE_CASE)
    private val bareClock = Regex("\\b(?:(\\d{1,2})[:.](\\d{2})\\s*(a\\.?m\\.?|p\\.?m\\.?)?|(\\d{1,2})\\s*(a\\.?m\\.?|p\\.?m\\.?)(?!\\w)|(noon|midnight))\\b", RegexOption.IGNORE_CASE)

    fun parse(text: String, now: ZonedDateTime, guess: Boolean = true): Command? {
        val m = prefix.matchEntire(text)
        if (m != null) return remind(m.groupValues[1], now, guess, "remind me")
        return call(text)
    }

    // ------------------------------------------------------------------ calls
    fun call(text: String): Command.Call? {
        val m = callCmd.matchEntire(text) ?: return null
        val verb = m.groupValues[1].lowercase()
        val who = m.groupValues[2].trim().replace(Regex("\\s+"), " ")
        val verbSure = verb in setOf("call", "phone", "ring", "dial")
        if (number.matches(who)) {
            val digits = who.filter { it.isDigit() || it == '+' }
            return if (digits.length in 5..16 && verbSure) Command.Call("number", digits) else null
        }
        val first = who.substringBefore(' ').lowercase()
        if (who.lowercase() in notATarget || !nameShape.matches(who)) return null
        // "a share", "the shop": may be a name the recogniser split up, or ordinary speech; a contact decides.
        val oddStart = first in setOf("a", "an", "the")
        if (first in notATarget && !oddStart) return null
        return Command.Call("name", who, sure = verbSure, loose = oddStart)
    }

    // ------------------------------------------------------------------ reminders
    private data class Clock(val hour: Int, val minute: Int, val ambiguous: Boolean)

    private fun clockOf(g: List<String>, hi: Int, mi: Int, ai: Int, wi: Int): Clock? {
        val word = g[wi].lowercase()
        if (word.isNotEmpty()) return if (word == "noon") Clock(12, 0, false) else Clock(0, 0, false)
        val h = g[hi].toIntOrNull() ?: return null
        val min = g[mi].toIntOrNull() ?: 0
        val ap = g[ai].lowercase().replace(".", "")
        if (min > 59 || h > 23) return null
        if (ap.isNotEmpty()) {
            if (h !in 1..12) return null
            return Clock(h % 12 + if (ap == "pm") 12 else 0, min, false)
        }
        return Clock(h, min, h in 1..12)
    }

    private fun remind(rest: String, now: ZonedDateTime, guess: Boolean, head: String): Command {
        val sofar = "$head $rest".trim()
        if (rest.isBlank()) return Command.Unclear("What should I remind you about?", sofar)
        var due: ZonedDateTime? = null
        val spans = ArrayList<IntRange>()
        val rel = relative.find(rest)
        if (rel != null) {
            val g = rel.groupValues
            val seconds: Long = when {
                g[1].isNotEmpty() -> 1800
                g[4].isNotEmpty() -> 3600
                else -> {
                    val n = g[2].toIntOrNull() ?: words[g[2].lowercase()] ?: 1
                    val u = g[3].lowercase()
                    n * when {
                        u.startsWith("sec") -> 1L
                        u.startsWith("min") -> 60L
                        u.startsWith("hour") || u.startsWith("hr") -> 3600L
                        u.startsWith("day") -> 86400L
                        else -> 604800L
                    }
                }
            }
            if (seconds < 10) return Command.Unclear("That is too soon to be a reminder.", sofar)
            due = now.plusSeconds(seconds); spans += rel.range
        } else {
            val dayM = dayWord.find(rest)
            val atM = atClock.find(rest)
            val bareM = if (atM == null) bareClock.find(rest) else null
            if (dayM == null && atM == null && bareM == null) return Command.Unclear("When should I remind you? Say, for example, “in 20 minutes” or “at 5 pm”.", sofar)
            val day = dayM?.groupValues?.get(1)?.lowercase() ?: ""
            if (dayM != null) spans += dayM.range
            var clock: Clock? = null
            when {
                atM != null -> { clock = clockOf(atM.groupValues, 1, 2, 3, 4); spans += atM.range }
                bareM != null -> {
                    val g = bareM.groupValues
                    clock = if (g[6].isNotEmpty()) clockOf(g, 0, 0, 0, 6) else if (g[1].isNotEmpty()) clockOf(g, 1, 2, 3, 6) else clockOf(g, 4, 0, 5, 6)
                    spans += bareM.range
                }
                day == "tonight" && guess -> clock = Clock(20, 0, false)
                (day == "tomorrow" || day in days) && guess -> clock = Clock(9, 0, false)
                else -> return Command.Unclear("I need a time of day with that, for example “${if (day.isEmpty()) "at 6 pm" else "$day at 6 pm"}”.", sofar)
            }
            if (clock == null) return Command.Unclear("I need a valid time, for example “at 5 pm” or “at 17:30”.", sofar)
            var hour = clock.hour
            if (clock.ambiguous) {
                if (!guess) return Command.Unclear("Is that morning or evening? Say “at 5 pm” or “at 17:00”.", sofar)
                if (day.isNotEmpty()) {
                    if (hour in 1..6) hour += 12
                } else {
                    val candidates = setOf(hour % 12, hour % 12 + 12).sorted().map { h ->
                        val c = now.withHour(h).withMinute(clock.minute).withSecond(0).withNano(0)
                        if (c.isAfter(now)) c else c.plusDays(1)
                    }
                    due = candidates.min()
                }
            }
            if (due == null) {
                var d = now.withHour(hour).withMinute(clock.minute).withSecond(0).withNano(0)
                when {
                    day == "tomorrow" -> d = d.plusDays(1)
                    day in days -> {
                        d = d.plusDays(((DayOfWeek.valueOf(day.uppercase()).value - now.dayOfWeek.value + 7) % 7).toLong())
                        if (!d.isAfter(now)) d = d.plusDays(7)
                    }
                    day == "today" || day == "tonight" -> if (!d.isAfter(now)) return Command.Unclear("That time has already passed today.", sofar)
                    else -> if (!d.isAfter(now)) d = d.plusDays(1)
                }
                due = d
            }
        }
        var what = rest
        for (r in spans.sortedByDescending { it.first }) what = what.substring(0, r.first) + " " + what.substring(r.last + 1)
        what = what.trim().replace(Regex("\\s+"), " ").trim(' ', ',', ':', '-')
        what = what.replace(Regex("\\s*\\b(?:on|at|by|for|in)\\s*$", RegexOption.IGNORE_CASE), "")
        what = what.replace(Regex("^(?:on|at|by|in)\\s+", RegexOption.IGNORE_CASE), "")
        repeat(3) { what = what.replace(Regex("^(?:to|that|about|for|of)\\s+", RegexOption.IGNORE_CASE), "").trim(' ', ',', ':', '-', '.') }
        if (what.isEmpty()) return Command.Unclear("What should I remind you about?", sofar)
        return Command.Remind(what.take(300), due!!)
    }
}
