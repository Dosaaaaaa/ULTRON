package com.ultron.assistant.data

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import com.ultron.assistant.service.ReminderReceiver
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

data class LocalReminder(val id: String, val text: String, val dueMillis: Long)

/** Reminders that live on this phone and fire with a real alarm, so they work with no computer, no
 *  network and after a reboot. Stored in the app's private preferences. */
class LocalReminders(private val context: Context) {
    private val prefs = context.getSharedPreferences("local_reminders", Context.MODE_PRIVATE)

    fun all(): List<LocalReminder> = load().sortedBy { it.dueMillis }

    fun add(text: String, dueMillis: Long): LocalReminder {
        val r = LocalReminder(UUID.randomUUID().toString(), text.trim().take(300), dueMillis)
        save(load() + r)
        schedule(r)
        return r
    }

    fun remove(id: String) {
        cancelAlarm(id)
        save(load().filter { it.id != id })
    }

    /** Called when an alarm has fired: forget it. */
    fun fired(id: String): LocalReminder? {
        val r = load().firstOrNull { it.id == id }
        save(load().filter { it.id != id })
        return r
    }

    /** After a reboot or an app update the alarms are gone: set them again (past-due ones fire now). */
    fun rescheduleAll() = load().forEach { schedule(it.copy(dueMillis = maxOf(it.dueMillis, System.currentTimeMillis() + 2000))) }

    private fun pending(id: String, text: String = ""): PendingIntent =
        PendingIntent.getBroadcast(
            context, id.hashCode(),
            Intent(context, ReminderReceiver::class.java).putExtra("id", id).putExtra("text", text),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    private fun schedule(r: LocalReminder) {
        val am = context.getSystemService(AlarmManager::class.java)
        val pi = pending(r.id, r.text)
        val exact = Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()
        if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.dueMillis, pi)
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.dueMillis, pi) // a few minutes late at worst
    }

    private fun cancelAlarm(id: String) {
        context.getSystemService(AlarmManager::class.java).cancel(pending(id))
    }

    private fun load(): List<LocalReminder> = runCatching {
        val arr = JSONArray(prefs.getString("items", "[]"))
        (0 until arr.length()).map { arr.getJSONObject(it) }.map { LocalReminder(it.getString("id"), it.getString("text"), it.getLong("due")) }
    }.getOrDefault(emptyList())

    private fun save(items: List<LocalReminder>) {
        val arr = JSONArray()
        items.forEach { arr.put(JSONObject().put("id", it.id).put("text", it.text).put("due", it.dueMillis)) }
        prefs.edit().putString("items", arr.toString()).apply()
    }
}
