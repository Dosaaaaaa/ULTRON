package com.ultron.assistant.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.ultron.assistant.data.Store

/** After a reboot or an app update, reconnect if the phone is paired and the operator left it on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        com.ultron.assistant.data.LocalReminders(context).rescheduleAll() // alarms do not survive a reboot
        val store = Store(context)
        if (store.load() != null && store.keepConnected) PushService.start(context)
    }
}
