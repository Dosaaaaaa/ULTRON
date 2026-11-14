package com.ultron.assistant.service

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.ultron.assistant.core.MorningTrigger
import com.ultron.assistant.data.Store
import java.time.LocalDate
import java.time.LocalTime

/** Lets ULTRON see notifications, once the operator allows "notification access" for it. It looks only at the apps on
 *  the list (Messages, WhatsApp, Gmail, Teams): for every other app it reads nothing at all. Codes and bank messages are
 *  dropped before anything is stored (see core.NoteFilter). */
class NotificationReader : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val n = sbn?.notification ?: return
        val pkg = sbn.packageName ?: return
        if (pkg == packageName || !NoteIngest.listed(pkg)) return
        // A group summary ("3 new messages") and ongoing status entries are not messages.
        if (n.flags and (Notification.FLAG_GROUP_SUMMARY or Notification.FLAG_ONGOING_EVENT) != 0) return
        val extras = n.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_TEXT) ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT))?.toString().orEmpty()
        NoteIngest.offer(this, pkg, title, text, sbn.postTime)
    }

    /** The Clock app's alarm notification going away means the alarm was dismissed: read the summary. */
    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        val n = sbn?.notification ?: return
        val pkg = sbn.packageName ?: return
        val store = Store(this)
        if (MorningTrigger.shouldFire(store.morningSummary, pkg, n.category, LocalTime.now().hour, LocalDate.now().toString(), store.morningDoneDay)) {
            NoteIngest.speakMorning(this)
        }
    }
}
