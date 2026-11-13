package com.ultron.assistant.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ServiceCompat
import com.ultron.assistant.data.Store
import com.ultron.assistant.net.Http
import com.ultron.assistant.net.PushClient

/** Foreground service (with its required notification) that keeps one connection to the operator's
 *  own ULTRON server so reminders and call confirmations arrive at once. It does nothing else: no
 *  microphone, no contacts, no calls. Stop it from the app or from the notification shade. */
class PushService : Service() {
    private var client: PushClient? = null
    private val main = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Notifier.channels(this)
        // The typed foreground service exists from Android 14; earlier versions take no type.
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, Notifier.STATUS_ID, Notifier.status(this, "Connecting…"), type)
        val pairing = Store(this).load()
        if (pairing == null || !Store(this).keepConnected) {
            stopSelf()
            return START_NOT_STICKY
        }
        client?.stop()
        client = PushClient(
            Http.client(pairing.fingerprint), pairing.baseUrl, pairing.token,
            scheduler = { delay, task -> main.postDelayed(task, delay) },
            onPush = { if (it is com.ultron.assistant.core.Push.CallProposal) AutoDialer.handle(applicationContext, it) else Notifier.show(applicationContext, it) },
            onStatus = { ok, detail ->
                LinkStatus.update(ok, detail)
                Notifier.update(applicationContext, if (ok) "Connected" else "Reconnecting… ($detail)")
            },
        ).also { it.start() }
        return START_STICKY
    }

    override fun onDestroy() {
        LinkStatus.stopped()
        client?.stop()
        client = null
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    companion object {
        fun start(context: Context) {
            context.startForegroundService(Intent(context, PushService::class.java))
        }

        fun stop(context: Context) {
            @Suppress("ImplicitSamInstance") // lint misfire: this is an Intent, not a lambda
            val intent = Intent(context, PushService::class.java)
            context.stopService(intent)
        }
    }
}
