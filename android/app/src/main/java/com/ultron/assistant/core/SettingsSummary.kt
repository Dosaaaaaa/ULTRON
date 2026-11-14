package com.ultron.assistant.core

/** The one-line summaries shown on the collapsed groups of the Settings screen. */
object SettingsSummary {
    fun permissions(missing: Int, total: Int): String = when {
        total == 0 -> "None needed"
        missing == 0 -> "All $total allowed"
        missing == 1 -> "1 needs your OK"
        else -> "$missing need your OK"
    }

    fun calls(confirmCalls: Boolean) = if (confirmCalls) "Calls: asks you first" else "Calls: start after a 3-second countdown"

    fun voice(name: String, chime: Boolean) = "Name: $name, chime ${if (chime) "on" else "off"}"

    fun computer(paired: Boolean, connected: Boolean) = when {
        !paired -> "Not connected"
        connected -> "Connected"
        else -> "Not reachable now"
    }

    fun notifications(access: Boolean, kept: Int) = when {
        !access -> "Not allowed yet"
        kept == 0 -> "Allowed, nothing kept"
        else -> "Allowed, $kept kept"
    }
}
