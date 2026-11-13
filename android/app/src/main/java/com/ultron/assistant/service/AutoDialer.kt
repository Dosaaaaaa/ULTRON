package com.ultron.assistant.service

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.telecom.TelecomManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.ultron.assistant.R
import com.ultron.assistant.core.AutoCall
import com.ultron.assistant.core.CallPlan
import com.ultron.assistant.core.Push
import com.ultron.assistant.data.ContactsReader

/** Places a call without a tap, ONLY when the operator switched "confirm before calling" off and the
 *  target is unambiguous. It always starts with a visible countdown that can be cancelled, and any
 *  doubt (no permission, no or several matching contacts, an old request) falls back to the normal
 *  confirmation notification. */
object AutoDialer {
    const val COUNTDOWN_MILLIS = 3000L
    private const val ACTION_CANCEL = "com.ultron.assistant.CANCEL_CALL"
    private const val EXTRA_KEY = "key"
    private val main = Handler(Looper.getMainLooper())
    private val pending = HashMap<String, Runnable>()

    /** Called for every incoming call proposal. */
    fun handle(context: Context, p: Push.CallProposal): CallPlan {
        val appContext = context.applicationContext
        fun has(permission: String) = ContextCompat.checkSelfPermission(appContext, permission) == PackageManager.PERMISSION_GRANTED
        val plan = AutoCall.plan(
            p, System.currentTimeMillis(), has(Manifest.permission.CALL_PHONE),
            if (has(Manifest.permission.READ_CONTACTS)) ({ ContactsReader.read(appContext) }) else null,
        )
        when (plan) {
            is CallPlan.Ask -> if (plan.notify) Notifier.show(appContext, p) // the operator confirms on the phone
            is CallPlan.Dial -> countdown(appContext, p, plan)
        }
        return plan
    }

    private fun countdown(context: Context, p: Push.CallProposal, plan: CallPlan.Dial) {
        val nm = context.getSystemService(NotificationManager::class.java)
        val nid = 2000 + (p.id.hashCode() and 0x0FFFFFFF)
        val cancel = PendingIntent.getBroadcast(
            context, nid,
            Intent(context, CallCancelReceiver::class.java).setAction(ACTION_CANCEL).putExtra(EXTRA_KEY, p.id),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val shown = NotificationCompat.Builder(context, Notifier.ALERT_CHANNEL)
            .setSmallIcon(R.drawable.ic_ultron).setContentTitle("Calling ${plan.label} in ${COUNTDOWN_MILLIS / 1000} s")
            .setContentText(plan.number).setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_HIGH).setOngoing(true).setOnlyAlertOnce(true)
            .addAction(0, "Cancel", cancel).setTimeoutAfter(COUNTDOWN_MILLIS + 5000)
        if (Notifier.canPost(context)) nm.notify(nid, shown.build())
        val go = Runnable {
            pending.remove(p.id)
            val ok = place(context, plan.number)
            if (Notifier.canPost(context)) {
                nm.notify(
                    nid,
                    NotificationCompat.Builder(context, Notifier.ALERT_CHANNEL).setSmallIcon(R.drawable.ic_ultron)
                        .setContentTitle(if (ok) "Calling ${plan.label}" else "Could not place the call to ${plan.label}")
                        .setContentText(plan.number).setAutoCancel(true).setTimeoutAfter(15000).build(),
                )
            }
            if (!ok) Notifier.show(context, p) // let the operator confirm instead
        }
        pending[p.id] = go
        main.postDelayed(go, COUNTDOWN_MILLIS)
    }

    /** True if Android accepted the call. Uses the telecom service, which does not need a visible activity. */
    private fun place(context: Context, number: String): Boolean = try {
        context.getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel", number, null), Bundle())
        true
    } catch (e: SecurityException) {
        false
    } catch (e: RuntimeException) {
        false
    }

    fun cancel(context: Context, key: String) {
        pending.remove(key)?.let { main.removeCallbacks(it) }
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.cancel(2000 + (key.hashCode() and 0x0FFFFFFF))
    }
}

class CallCancelReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        intent.getStringExtra("key")?.let { AutoDialer.cancel(context, it) }
    }
}
