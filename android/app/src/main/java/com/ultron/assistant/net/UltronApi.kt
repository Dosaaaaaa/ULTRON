package com.ultron.assistant.net

import com.ultron.assistant.core.ChatParser
import com.ultron.assistant.core.ChatReply
import com.ultron.assistant.core.ReminderItem
import com.ultron.assistant.data.Pairing
import java.io.IOException
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** call_confirm: ask before dialling; reminder_guess: pick sensible times instead of asking. */
data class Prefs(val callConfirm: Boolean, val reminderGuess: Boolean)

class ApiError(val status: Int, message: String) : IOException(message)

/** The few REST calls the phone makes. Blocking: call from a background thread. */
class UltronApi(private val http: OkHttpClient, private val baseUrl: String, private val token: String? = null) {
    private val json = "application/json".toMediaType()

    private fun send(builder: Request.Builder): String {
        if (token != null) builder.header("Authorization", "Bearer $token")
        http.newCall(builder.build()).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) {
                val msg = runCatching { JSONObject(text).getJSONObject("error").getString("message") }.getOrNull()
                throw ApiError(r.code, msg ?: "The server answered ${r.code}.")
            }
            return text
        }
    }

    private fun call(path: String, body: JSONObject): JSONObject =
        JSONObject(send(Request.Builder().url(baseUrl + path).post(body.toString().toRequestBody(json))))

    fun pair(code: String, name: String, fingerprint: String?): Pairing {
        val o = call("/v1/devices/pair", JSONObject().put("code", code).put("name", name).put("kind", "phone"))
        val caps = o.optJSONArray("capabilities")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
        return Pairing(baseUrl, fingerprint, o.getString("token"), o.getString("device_id"), caps)
    }

    /** One chat turn: the reply, the conversation to continue, and anything ULTRON did on a command. */
    fun chat(message: String, conversationId: String?, voice: Boolean): ChatReply {
        val body = JSONObject().put("message", message).put("modality", if (voice) "voice" else "text")
        if (conversationId != null) body.put("conversation_id", conversationId)
        return ChatParser.reply(call("/v1/chat", body))
    }

    /** Needs the `reminders` power; the server answers 403 otherwise. */
    fun reminders(): List<ReminderItem> =
        ChatParser.reminders(org.json.JSONArray(send(Request.Builder().url("$baseUrl/v1/reminders").get())))

    fun addReminder(text: String, dueAtIso: String): ReminderItem =
        ChatParser.reminders(org.json.JSONArray().put(call("/v1/reminders", JSONObject().put("text", text).put("due_at", dueAtIso))))
            .first()

    /** The two "ask before acting" switches (owner-wide; any paired phone with chat may read and change them). */
    fun preferences(): Prefs = prefsOf(JSONObject(send(Request.Builder().url("$baseUrl/v1/preferences").get())))

    fun setPreferences(callConfirm: Boolean? = null, reminderGuess: Boolean? = null): Prefs {
        val body = JSONObject()
        callConfirm?.let { body.put("call_confirm", it) }
        reminderGuess?.let { body.put("reminder_guess", it) }
        return prefsOf(JSONObject(send(Request.Builder().url("$baseUrl/v1/preferences").patch(body.toString().toRequestBody(json)))))
    }

    private fun prefsOf(o: JSONObject) = Prefs(o.optBoolean("call_confirm", true), o.optBoolean("reminder_guess", false))

    fun cancelReminder(id: String) {
        send(Request.Builder().url("$baseUrl/v1/reminders/$id").delete())
    }
}
