package com.ultron.assistant.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.ultron.assistant.core.Note
import com.ultron.assistant.core.NoteLog

/** The notifications ULTRON kept (see [com.ultron.assistant.core.NoteFilter]): on this phone only, in Keystore-backed
 *  encrypted preferences that are excluded from backups, for 48 hours at most. Also counts, never the text, of what the
 *  filter dropped (codes, bank and payment messages). */
class NoteStore(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "ultron_notes",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun all(now: Long = System.currentTimeMillis()): List<Note> = synchronized(lock) {
        NoteLog.prune(NoteLog.decode(prefs.getString("notes", "").orEmpty()), now)
    }

    fun add(note: Note) = synchronized(lock) {
        val list = NoteLog.add(NoteLog.prune(NoteLog.decode(prefs.getString("notes", "").orEmpty()), note.whenMs), note)
        prefs.edit().putString("notes", NoteLog.encode(list)).apply()
    }

    fun countDropped() = synchronized(lock) { prefs.edit().putInt("dropped", dropped + 1).apply() }

    val dropped: Int get() = prefs.getInt("dropped", 0)

    /** Forget every kept note and the dropped counter. */
    fun clear() = synchronized(lock) { prefs.edit().clear().apply() }

    private companion object { val lock = Any() }
}
