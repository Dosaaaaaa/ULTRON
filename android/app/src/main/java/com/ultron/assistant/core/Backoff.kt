package com.ultron.assistant.core

/** Reconnect delays: 1 s doubling to a 60 s ceiling, with up to 25% jitter so a restarted backend is
 *  not hit by every device in the same instant. */
class Backoff(private val random: () -> Double = { Math.random() }) {
    private var attempt = 0

    fun nextMillis(): Long {
        val base = minOf(60_000L, 1_000L shl minOf(attempt, 6))
        attempt++
        return base + (base * 0.25 * random()).toLong()
    }

    fun reset() { attempt = 0 }
}

/** Remembers the last N push ids so the same item (re-sent until acknowledged) is shown once. */
class Seen(private val capacity: Int = 500) {
    private val ids = object : LinkedHashMap<String, Unit>(16, 0.75f, false) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Unit>?) = size > capacity
    }

    @Synchronized fun firstTime(id: String): Boolean = ids.put(id, Unit) == null
}
