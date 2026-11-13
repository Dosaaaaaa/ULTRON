package com.ultron.assistant.core

import org.json.JSONObject

data class ChatAction(val type: String, val status: String, val summary: String)

data class ChatReply(val text: String, val conversationId: String?, val actions: List<ChatAction>)

data class ReminderItem(val id: String, val text: String, val dueAt: String, val status: String)

object ChatParser {
    fun reply(o: JSONObject): ChatReply {
        val actions = ArrayList<ChatAction>()
        val arr = o.optJSONArray("actions")
        if (arr != null) for (i in 0 until minOf(arr.length(), 5)) {
            val a = arr.optJSONObject(i) ?: continue
            val type = PushParser.clean(a.optString("type"), 20)
            val status = PushParser.clean(a.optString("status"), 30)
            val summary = when {
                type == "call" && status == "awaiting_confirmation" -> "A call confirmation was sent to this phone."
                type == "call" && status == "placing" -> "Calling now. You can cancel during the countdown."
                type == "call" -> "No phone is allowed to make calls, so nothing happened."
                type == "reminder" && status == "created" -> "Reminder set for ${PushParser.clean(a.optString("due_local"), 60)}."
                type == "reminder" -> "No reminder was set: ${PushParser.clean(a.optString("why"), 160)}"
                else -> continue
            }
            actions += ChatAction(type, status, summary)
        }
        return ChatReply(o.getString("reply"), o.optString("conversation_id").ifEmpty { null }, actions)
    }

    fun reminders(arr: org.json.JSONArray): List<ReminderItem> =
        (0 until minOf(arr.length(), 200)).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            val id = o.optString("id")
            if (id.isEmpty()) null
            else ReminderItem(id, PushParser.clean(o.optString("text"), 300), PushParser.clean(o.optString("due_at"), 40), o.optString("status"))
        }
}
