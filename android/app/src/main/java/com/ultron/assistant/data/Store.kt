package com.ultron.assistant.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** The pairing: server address, pinned certificate fingerprint and this phone's own token. Kept in
 *  Keystore-backed encrypted preferences and excluded from backups and device transfers. */
data class Pairing(
    val baseUrl: String,
    val fingerprint: String?,
    val token: String,
    val deviceId: String,
    val capabilities: List<String> = emptyList(),
)

class Store(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "ultron_pairing",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun load(): Pairing? {
        val url = prefs.getString("url", null) ?: return null
        val token = prefs.getString("token", null) ?: return null
        val id = prefs.getString("device_id", null) ?: return null
        val caps = prefs.getString("caps", "").orEmpty().split(',').filter { it.isNotEmpty() }
        return Pairing(url, prefs.getString("fingerprint", null), token, id, caps)
    }

    fun save(p: Pairing) {
        prefs.edit().putString("url", p.baseUrl).putString("token", p.token)
            .putString("device_id", p.deviceId).putString("fingerprint", p.fingerprint)
            .putString("caps", p.capabilities.joinToString(",")).apply()
    }

    /** Forget the computer (settings and local reminders are kept). */
    fun clear() = prefs.edit().remove("url").remove("token").remove("device_id").remove("fingerprint").remove("caps").remove("conversation").apply()

    /** The conversation the phone continues, so a restart does not start a new one. */
    var conversationId: String?
        get() = prefs.getString("conversation", null)
        set(v) = prefs.edit().apply { if (v == null) remove("conversation") else putString("conversation", v) }.apply()

    /** Calls and reminders are done without asking by default; these switches turn review on. */
    var confirmCalls: Boolean
        get() = prefs.getBoolean("confirm_calls", false)
        set(v) = prefs.edit().putBoolean("confirm_calls", v).apply()

    /** Reminders with a vague time ("tomorrow", "at 5") get a sensible time instead of a question. */
    var guessTimes: Boolean
        get() = prefs.getBoolean("guess_times", true)
        set(v) = prefs.edit().putBoolean("guess_times", v).apply()

    /** Whether replies set up by voice are read aloud. */
    var speakReplies: Boolean
        get() = prefs.getBoolean("speak_replies", true)
        set(v) = prefs.edit().putBoolean("speak_replies", v).apply()

    /** Whether the always-on connection should run (the operator can turn it off). */
    var keepConnected: Boolean
        get() = prefs.getBoolean("keep_connected", true)
        set(v) = prefs.edit().putBoolean("keep_connected", v).apply()

    /** Hands-free mode: say "Ultron" and a command with no tap. Off until the operator turns it on. */
    var handsFree: Boolean
        get() = prefs.getBoolean("hands_free", false)
        set(v) = prefs.edit().putBoolean("hands_free", v).apply()

    /** The name that wakes hands-free mode. */
    var wakeName: String
        get() {
            // One time, for the 1.7.0 change of the default name (see WakeWord.migrate).
            com.ultron.assistant.core.WakeWord.migrate(prefs.getString("wake_name", null), prefs.getBoolean("name_170", false))?.let {
                prefs.edit().putString("wake_name", it).apply()
            }
            if (!prefs.getBoolean("name_170", false)) prefs.edit().putBoolean("name_170", true).apply()
            return prefs.getString("wake_name", com.ultron.assistant.core.WakeWord.DEFAULT) ?: com.ultron.assistant.core.WakeWord.DEFAULT
        }
        set(v) { prefs.edit().putString("wake_name", v).putBoolean("name_170", true).apply() }

    /** How the offline recogniser writes THIS person saying the wake name, learned in voice training. */
    fun wakeAliases(name: String): List<String> =
        prefs.getString("wake_alias_" + name.lowercase(), "").orEmpty().split(',').filter { it.isNotBlank() }

    fun setWakeAliases(name: String, aliases: List<String>) =
        prefs.edit().putString("wake_alias_" + name.lowercase(), aliases.joinToString(",")).apply()

    /** Play a short sound when the wake name is heard, so the operator knows to speak now. */
    var soundOnWake: Boolean
        get() = prefs.getBoolean("sound_on_wake", true)
        set(v) = prefs.edit().putBoolean("sound_on_wake", v).apply()

    /** Package of the app "play ..." uses; empty = the first installed one that can play by voice. */
    var musicApp: String
        get() = prefs.getString("music_app", "") ?: ""
        set(v) = prefs.edit().putString("music_app", v).apply()

    /** After the wake name alone, the command is heard by Google's recogniser, which copes far better with accents,
     *  names and song titles than the small offline one. Only that command's audio is sent, never the wake-up
     *  listening. Off = everything stays on the phone. */
    var onlineCommands: Boolean
        get() = prefs.getBoolean("online_commands", false)
        set(v) = prefs.edit().putBoolean("online_commands", v).apply()

    /** Short commands (pause, next, louder...) run as soon as the speaker goes quiet, not after the listener's longer pause. */
    var quickCommands: Boolean
        get() = prefs.getBoolean("quick_commands", true)
        set(v) = prefs.edit().putBoolean("quick_commands", v).apply()

    /** Which kinds of notification may be read (core.Source keys). All four until the operator switches one off. */
    var noteSources: Set<String>
        get() = prefs.getStringSet("note_sources", null)?.toSet() ?: com.ultron.assistant.core.Source.entries.map { it.key }.toSet()
        set(v) = prefs.edit().putStringSet("note_sources", v).apply()

    /** Read the summary by itself when the morning alarm is dismissed. */
    var morningSummary: Boolean
        get() = prefs.getBoolean("morning_summary", true)
        set(v) = prefs.edit().putBoolean("morning_summary", v).apply()

    var morningDoneDay: String
        get() = prefs.getString("morning_done", "") ?: ""
        set(v) = prefs.edit().putString("morning_done", v).apply()

    /** When each kind of note was last told to the operator (so "what did I miss" does not repeat itself). */
    fun notesSeen(): Map<com.ultron.assistant.core.Source, Long> =
        com.ultron.assistant.core.Source.entries.associateWith { prefs.getLong("seen_" + it.key, 0L) }

    fun markSeen(kinds: Collection<com.ultron.assistant.core.Source>, at: Long) =
        prefs.edit().also { e -> kinds.forEach { e.putLong("seen_" + it.key, at) } }.apply()

    /** The text-to-speech engine that speaks replies (a package name); empty = the phone's default engine. */
    var ttsEngine: String
        get() = prefs.getString("tts_engine", "") ?: ""
        set(v) = prefs.edit().putString("tts_engine", v).apply()

    /** The microphone button in the app: false = the offline recogniser on the phone (nothing leaves it),
     *  true = Android's speech recogniser (Google's, better with long free sentences). */
    var googleTalk: Boolean
        get() = prefs.getBoolean("google_talk", false)
        set(v) = prefs.edit().putBoolean("google_talk", v).apply()

    /** Saying the wake name while ULTRON is talking makes it stop and listen. */
    var bargeIn: Boolean
        get() = prefs.getBoolean("barge_in", true)
        set(v) = prefs.edit().putBoolean("barge_in", v).apply()

    /** While music is playing, the wake name counts anywhere in what is heard (there is no silence to start a
     *  sentence from), and hearing it pauses the music. Off = the name only counts at the start of a sentence. */
    var wakeOverMusic: Boolean
        get() = prefs.getBoolean("wake_over_music", true)
        set(v) = prefs.edit().putBoolean("wake_over_music", v).apply()

    /** The offline speech model: "us" (bundled) or "in" (Indian English, if bundled). */
    var speechModel: String
        get() = prefs.getString("speech_model", "us") ?: "us"
        set(v) = prefs.edit().putString("speech_model", v).apply()

    /** How the recogniser wrote a contact's name when this person said it: "a share" -> Asha. Stays on the phone. */
    fun contactAliases(): Map<String, String> =
        runCatching {
            val o = org.json.JSONObject(prefs.getString("contact_aliases", "{}") ?: "{}")
            o.keys().asSequence().associateWith { o.getString(it) }
        }.getOrDefault(emptyMap())

    fun learnContactAlias(heard: String, contactName: String) {
        val key = com.ultron.assistant.core.ContactAliases.key(heard)
        if (key.length < 2) return
        val all = LinkedHashMap(contactAliases())
        all.remove(key)
        all[key] = contactName
        while (all.size > 200) all.remove(all.keys.first())
        prefs.edit().putString("contact_aliases", org.json.JSONObject(all as Map<*, *>).toString()).apply()
    }
}
