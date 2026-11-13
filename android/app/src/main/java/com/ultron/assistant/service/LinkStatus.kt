package com.ultron.assistant.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** What the background connection is doing right now, shared with the screens (same process). */
data class Link(val connected: Boolean, val detail: String, val running: Boolean)

object LinkStatus {
    private val _state = MutableStateFlow(Link(connected = false, detail = "not started", running = false))
    val state: StateFlow<Link> = _state

    fun update(connected: Boolean, detail: String) { _state.value = Link(connected, detail, running = true) }
    fun stopped() { _state.value = Link(connected = false, detail = "stopped", running = false) }
}
