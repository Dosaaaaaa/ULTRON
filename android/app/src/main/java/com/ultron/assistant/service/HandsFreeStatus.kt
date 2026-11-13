package com.ultron.assistant.service

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** What hands-free mode is doing right now, so the app can show the truth instead of a switch that
 *  only records what the operator asked for. Lives in memory only. */
object HandsFreeStatus {
    /** True from the moment the service begins to listen until it stops. */
    @Volatile var active = false
    var text by mutableStateOf("Off")

    /** Only while the Settings screen is open: show what the listener wrote for the last thing it heard,
     *  so a missed wake can be understood. Off otherwise; nothing is kept. */
    @Volatile var showHeard = false
    var heard by mutableStateOf("")

    fun set(active: Boolean, text: String) { this.active = active; this.text = text }
}
