package com.ultron.assistant.core

/** Cleans up how the offline recogniser writes spoken commands, before they are parsed.
 *
 *  Measured on synthetic speech (docs/PHASE_5_RESULTS.md): "in ten minutes" comes out as "and ten
 *  minutes", times as words ("five thirty p m"), "remind me" as "reminds me". The parsers want digits
 *  and plain words, so those quirks are undone here, only where the surrounding words make the meaning
 *  clear ("play one direction" keeps its "one"). */
object Spoken {
    private val units = mapOf(
        "zero" to 0, "oh" to 0, "one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5, "six" to 6, "seven" to 7,
        "eight" to 8, "nine" to 9, "ten" to 10, "eleven" to 11, "twelve" to 12, "thirteen" to 13, "fourteen" to 14,
        "fifteen" to 15, "sixteen" to 16, "seventeen" to 17, "eighteen" to 18, "nineteen" to 19,
    )
    private val tens = mapOf("twenty" to 20, "thirty" to 30, "forty" to 40, "fifty" to 50, "sixty" to 60, "seventy" to 70, "eighty" to 80, "ninety" to 90)
    private val digitWords = setOf("zero", "oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    private val lead = setOf("at", "for", "in", "by", "around", "after", "than", "about", "within", "past", "to")
    private val unitsAfter = Regex("^(?:am|pm|a|p|o'?clock|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|percent)$", RegexOption.IGNORE_CASE)

    private fun core(token: String) = token.trimEnd('.', ',', '?', '!', ';', ':').lowercase()
    private fun trail(token: String) = token.substring(token.trimEnd('.', ',', '?', '!', ';', ':').length)
    private fun isNum(w: String) = w in units || w in tens || w == "double" || w == "triple"

    fun fix(raw: String): String {
        var s = raw.trim().replace(Regex("\\s+"), " ")
        if (s.isEmpty()) return s
        s = s.replace(Regex("\\b(?:reminds|remain|remine|remined|reminder)\\s+(?:me|may|my)\\b", RegexOption.IGNORE_CASE), "remind me")
        s = s.replace(Regex("\\bremind\\s+(?:may|my)\\b", RegexOption.IGNORE_CASE), "remind me")
        s = numbers(s)
        // "and ten minutes" -> "in ten minutes"
        s = s.replace(Regex("\\band\\s+(?=(?:\\d+|an?|half an)\\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|hour)\\b)", RegexOption.IGNORE_CASE), "in ")
        // "half past 5", "quarter to 6"
        s = s.replace(Regex("\\bhalf past (\\d{1,2})\\b", RegexOption.IGNORE_CASE)) { "${it.groupValues[1]}:30" }
        s = s.replace(Regex("\\bquarter past (\\d{1,2})\\b", RegexOption.IGNORE_CASE)) { "${it.groupValues[1]}:15" }
        s = s.replace(Regex("\\bquarter to (\\d{1,2})\\b", RegexOption.IGNORE_CASE)) {
            val h = it.groupValues[1].toInt(); "${if (h == 1) 12 else h - 1}:45"
        }
        // "5 a m" / "5:30 p. m." -> "5am" / "5:30pm"
        s = s.replace(Regex("(?<=\\d)\\s*([ap])\\.?\\s*m\\b\\.?", RegexOption.IGNORE_CASE)) { "${it.groupValues[1].lowercase()}m" }
        s = s.replace(Regex("(\\d)\\s+o'?clock\\b", RegexOption.IGNORE_CASE), "$1")
        // "at 6 in the morning" -> "at 6am"
        s = s.replace(Regex("(\\d{1,2}(?::\\d{2})?)\\s+(?:in the |at )?morning\\b", RegexOption.IGNORE_CASE), "$1am")
        s = s.replace(Regex("(\\d{1,2}(?::\\d{2})?)\\s+(?:in the |at )?(?:afternoon|evening|night)\\b", RegexOption.IGNORE_CASE), "$1pm")
        return s
    }

    /** Number words to digits where a time, a length of time or a phone number is clearly meant. */
    private fun numbers(text: String): String {
        val tokens = text.split(' ')
        val out = ArrayList<String>()
        var i = 0
        while (i < tokens.size) {
            if (!isNum(core(tokens[i]))) { out += tokens[i]; i++; continue }
            var j = i
            while (j < tokens.size && isNum(core(tokens[j]))) j++
            val run = tokens.subList(i, j).map { core(it) }
            val prev = if (i > 0) core(tokens[i - 1]) else ""
            val next = if (j < tokens.size) core(tokens[j]) else ""
            val tail = trail(tokens[j - 1])
            val phone = run.size >= 5 && run.all { it in digitWords || it == "double" || it == "triple" }
            val timing = prev in lead || unitsAfter.matches(next)
            if (phone) {
                val digits = StringBuilder()
                var k = 0
                while (k < run.size) {
                    val w = run[k]
                    when {
                        (w == "double" || w == "triple") && k + 1 < run.size -> { repeat(if (w == "double") 2 else 3) { digits.append(units[run[k + 1]]) }; k += 2 }
                        w in digitWords -> { digits.append(units[w]); k++ }
                        else -> k++
                    }
                }
                out += digits.toString() + tail
            } else if (timing) {
                val values = value(run)
                if (values == null) out += tokens.subList(i, j)
                else {
                    val clockPrev = prev == "at" || prev == "for" || prev == "by" || prev == "around"
                    val meridiem = next == "am" || next == "pm" || next == "a" || next == "p"
                    if (values.size == 2 && (clockPrev || meridiem) && values[0].first in 0..23 && values[1].first in 0..59 && (values[1].first >= 10 || values[1].second)) {
                        out += "${values[0].first}:${values[1].first.toString().padStart(2, '0')}$tail"
                    } else {
                        values.forEachIndexed { n, v -> out += v.first.toString() + if (n == values.lastIndex) tail else "" }
                    }
                }
            } else out += tokens.subList(i, j)
            i = j
        }
        return out.joinToString(" ")
    }

    /** "twenty five" -> [25], "five thirty" -> [5, 30], "six oh five" -> [6, 5(padded)]. Null if it makes no sense. */
    private fun value(run: List<String>): List<Pair<Int, Boolean>>? {
        val out = ArrayList<Pair<Int, Boolean>>()
        var k = 0
        while (k < run.size) {
            val w = run[k]
            when {
                w == "double" || w == "triple" -> return null
                w == "oh" && k + 1 < run.size && units[run[k + 1]] in 1..9 -> { out += units.getValue(run[k + 1]) to true; k += 2 }
                w in tens -> {
                    val unit = if (k + 1 < run.size) units[run[k + 1]] else null
                    if (unit != null && unit in 1..9 && run[k + 1] != "oh") { out += (tens.getValue(w) + unit) to false; k += 2 } else { out += tens.getValue(w) to false; k++ }
                }
                w in units -> { out += units.getValue(w) to false; k++ }
                else -> return null
            }
        }
        return out
    }
}
