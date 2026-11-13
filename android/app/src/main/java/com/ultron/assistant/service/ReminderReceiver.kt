package com.ultron.assistant.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.ultron.assistant.core.Push
import com.ultron.assistant.data.LocalReminders

/** An alarm set by LocalReminders went off: show the reminder (this is what makes it work with no computer). */
class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra("id") ?: return
        val stored = LocalReminders(context).fired(id)
        val text = stored?.text ?: intent.getStringExtra("text").orEmpty()
        if (text.isEmpty()) return
        Notifier.channels(context)
        Notifier.show(context, Push.Reminder(id, text, ""))
    }
}
