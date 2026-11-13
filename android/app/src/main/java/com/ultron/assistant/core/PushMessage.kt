package com.ultron.assistant.core

import org.json.JSONException
import org.json.JSONObject

/** What the backend pushes down the device socket (shared/protocols/device_push.md). Text is
 *  length-limited and stripped of control characters before it reaches a notification. */
sealed interface Push {
    val id: String

    data class Notice(override val id: String, val title: String, val body: String) : Push
    data class Reminder(override val id: String, val text: String, val dueAt: String) : Push
    data class CallProposal(
        override val id: String,
        val requestId: String,
        val kind: String, // "name" | "number"
        val target: String,
        val createdAtMillis: Long?,
        /** true: show a confirmation (the default); false: the operator turned confirmation off. */
        val confirm: Boolean = true,
    ) : Push
}

sealed interface Incoming {
    data class Item(val push: Push) : Incoming
    data object Pong : Incoming
    data class Failure(val code: String) : Incoming
    data object Ignored : Incoming
}

object PushParser {
    private val control = Regex("[\\p{Cntrl}&&[^\n]]")

    fun clean(text: String?, max: Int): String = (text ?: "").replace(control, " ").trim().take(max)

    fun parse(raw: String): Incoming {
        val o = try { JSONObject(raw) } catch (e: JSONException) { return Incoming.Ignored }
        return when (o.optString("type")) {
            "pong" -> Incoming.Pong
            "error" -> Incoming.Failure(clean(o.optString("code"), 40))
            "push" -> item(o)
            else -> Incoming.Ignored
        }
    }

    private fun item(o: JSONObject): Incoming {
        val id = clean(o.optString("id"), 64)
        val payload = o.optJSONObject("payload") ?: return Incoming.Ignored
        if (id.isEmpty()) return Incoming.Ignored
        val push: Push = when (o.optString("kind")) {
            "notify" -> Push.Notice(id, clean(payload.optString("title"), 80), clean(payload.optString("body"), 400))
            "reminder" -> Push.Reminder(id, clean(payload.optString("text"), 300), clean(payload.optString("due_at"), 40))
            "call_proposal" -> {
                val kind = payload.optString("kind")
                val target = clean(payload.optString("target"), 60)
                if (kind !in setOf("name", "number") || target.isEmpty()) return Incoming.Ignored
                Push.CallProposal(id, clean(payload.optString("request_id"), 40), kind, target, parseTime(o.optString("created_at")), payload.optBoolean("confirm", true))
            }
            else -> return Incoming.Ignored
        }
        return Incoming.Item(push)
    }

    private fun parseTime(text: String): Long? = try {
        java.time.OffsetDateTime.parse(text).toInstant().toEpochMilli()
    } catch (e: Exception) { null }
}
