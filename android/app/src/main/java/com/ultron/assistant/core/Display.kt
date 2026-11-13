package com.ultron.assistant.core

import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/** Text for the screen, kept apart from the UI so it can be tested. */
object Display {
    fun parseInstant(iso: String): Instant? = try { OffsetDateTime.parse(iso).toInstant() } catch (e: Exception) { null }

    /** "Today 17:58", "Tomorrow 09:00", "Mon 16 Nov 08:30", or "Overdue" wording is the caller's job. */
    fun when_(iso: String, now: Instant, zone: ZoneId, locale: Locale = Locale.getDefault()): String {
        val at = parseInstant(iso) ?: return iso
        val day = at.atZone(zone).toLocalDate()
        val today = now.atZone(zone).toLocalDate()
        val time = DateTimeFormatter.ofPattern("HH:mm", locale).format(at.atZone(zone))
        return when (ChronoUnit.DAYS.between(today, day)) {
            0L -> "Today $time"
            1L -> "Tomorrow $time"
            -1L -> "Yesterday $time"
            else -> DateTimeFormatter.ofPattern("EEE d MMM", locale).format(at.atZone(zone)) + " " + time
        }
    }

    /** "in 5 min", "in 2 h", "in 3 days", "due now" / "5 min ago". */
    fun distance(iso: String, now: Instant): String {
        val at = parseInstant(iso) ?: return ""
        val secs = at.epochSecond - now.epochSecond
        val abs = Math.abs(secs)
        val text = when {
            abs < 60 -> return if (secs >= 0) "due now" else "just now"
            abs < 90 * 60 -> "${abs / 60} min"
            abs < 36 * 3600 -> "${Math.round(abs / 3600.0)} h"
            else -> "${Math.round(abs / 86400.0)} days"
        }
        return if (secs >= 0) "in $text" else "$text ago"
    }
}
