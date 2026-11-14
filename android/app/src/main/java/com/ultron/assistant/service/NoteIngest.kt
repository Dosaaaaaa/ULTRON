package com.ultron.assistant.service

import android.content.Context
import androidx.core.app.NotificationManagerCompat
import com.ultron.assistant.BuildConfig
import com.ultron.assistant.core.Briefing
import com.ultron.assistant.core.Note
import com.ultron.assistant.core.NoteFilter
import com.ultron.assistant.core.NoteLog
import com.ultron.assistant.core.ReadCommand
import com.ultron.assistant.core.Source
import com.ultron.assistant.core.Verdict
import com.ultron.assistant.data.NoteStore
import com.ultron.assistant.data.Store
import com.ultron.assistant.ui.Voice
import java.time.LocalDate
import java.time.LocalTime

/** Everything between "a notification arrived" and "ULTRON can tell you about it". The text of a notification is
 *  never logged. */
object NoteIngest {
    /** The shell's own notifications stand in for an app on the emulator in debug builds. Never in release. */
    private val debugSources = if (BuildConfig.DEBUG) mapOf("com.android.shell" to Source.SMS) else emptyMap()

    fun accessGranted(context: Context) = NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)

    /** Only apps on the list are looked at at all: the caller reads no text of any other app. */
    fun listed(pkg: String) = NoteFilter.source(pkg) != null || pkg in debugSources

    /** Judges one notification and keeps it, counts it as dropped, or ignores it. True if it was kept. */
    fun offer(context: Context, pkg: String, title: String, text: String, whenMs: Long): Boolean {
        val verdict = NoteFilter.judge(pkg, title, text, debugSources)
        val notes = NoteStore(context)
        return when (verdict) {
            Verdict.Ignore -> false
            is Verdict.Drop -> { notes.countDropped(); false }
            is Verdict.Keep -> if (verdict.source.key !in Store(context).noteSources) false
            else { notes.add(Note(verdict.source, NoteLog.sender(title), NoteLog.clip(text), whenMs)); true }
        }
    }

    /** What to say for [cmd], and the bookkeeping that goes with it. */
    fun answer(context: Context, cmd: ReadCommand): String {
        if (cmd == ReadCommand.Clear) { NoteStore(context).clear(); return Briefing.answer(cmd, emptyList(), emptyMap(), 0).text }
        if (!accessGranted(context)) return "I cannot see notifications yet. Open ULTRON settings, then More settings, then Notifications, and allow notification access."
        val store = Store(context)
        val now = System.currentTimeMillis()
        val a = Briefing.answer(cmd, NoteStore(context).all(now), store.notesSeen(), LocalTime.now().hour)
        if (a.told.isNotEmpty()) store.markSeen(a.told, now)
        return a.text
    }

    /** The morning summary, read aloud. Once a day. */
    fun speakMorning(context: Context, onSaid: (String) -> Unit = {}) {
        val store = Store(context)
        store.morningDoneDay = LocalDate.now().toString()
        val text = answer(context, ReadCommand.Morning)
        onSaid(text)
        val voice = Voice(context)
        voice.speak(text) { voice.shutdown() }
    }
}
