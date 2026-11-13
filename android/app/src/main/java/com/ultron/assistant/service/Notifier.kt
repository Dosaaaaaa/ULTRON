package com.ultron.assistant.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.ultron.assistant.R
import com.ultron.assistant.core.Push
import com.ultron.assistant.ui.CallConfirmActivity
import com.ultron.assistant.ui.MainActivity

object Notifier {
    const val STATUS_CHANNEL = "status"
    const val ALERT_CHANNEL = "alerts"
    const val STATUS_ID = 1
    const val HANDS_FREE_ID = 4

    fun channels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(STATUS_CHANNEL, "Connection", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Shows that ULTRON is connected. Required by Android for an always-on connection."
                setShowBadge(false)
            },
        )
        nm.createNotificationChannel(
            NotificationChannel(ALERT_CHANNEL, "Reminders and calls", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Reminders, messages and call confirmations from ULTRON."
            },
        )
    }

    fun status(context: Context, text: String): Notification {
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(context, STATUS_CHANNEL)
            .setSmallIcon(R.drawable.ic_ultron).setContentTitle("ULTRON").setContentText(text)
            .setOngoing(true).setSilent(true).setContentIntent(open)
            .setPriority(NotificationCompat.PRIORITY_MIN).build()
    }

    fun update(context: Context, text: String) {
        if (canPost(context)) {
            context.getSystemService(NotificationManager::class.java).notify(STATUS_ID, status(context, text))
        }
    }

    fun canPost(context: Context) =
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** Same item id -> same notification id, so a re-sent item replaces itself instead of stacking. */
    fun show(context: Context, push: Push) {
        if (!canPost(context)) return
        val nid = 1000 + (push.id.hashCode() and 0x0FFFFFFF)
        val openApp = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val b = NotificationCompat.Builder(context, ALERT_CHANNEL)
            .setSmallIcon(R.drawable.ic_ultron).setAutoCancel(true).setContentIntent(openApp)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
        when (push) {
            is Push.Notice -> b.setContentTitle(push.title).setContentText(push.body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(push.body))
            is Push.Reminder -> b.setContentTitle("Reminder").setContentText(push.text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(push.text))
                .setCategory(NotificationCompat.CATEGORY_REMINDER)
            is Push.CallProposal -> {
                val intent = Intent(context, CallConfirmActivity::class.java)
                    .putExtra(CallConfirmActivity.EXTRA_ID, push.id).putExtra(CallConfirmActivity.EXTRA_KIND, push.kind)
                    .putExtra(CallConfirmActivity.EXTRA_TARGET, push.target)
                    .putExtra(CallConfirmActivity.EXTRA_CREATED, push.createdAtMillis ?: -1L)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                val pi = PendingIntent.getActivity(context, nid, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
                b.setContentTitle("Call ${push.target}?").setContentText("Tap to review and confirm. Nothing is dialled until you do.")
                    .setContentIntent(pi).setCategory(NotificationCompat.CATEGORY_CALL)
                    .addAction(0, "Review", pi).setTimeoutAfter(5 * 60 * 1000L)
            }
        }
        context.getSystemService(NotificationManager::class.java).notify(nid, b.build())
    }
}
