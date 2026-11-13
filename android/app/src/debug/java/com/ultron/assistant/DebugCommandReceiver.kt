package com.ultron.assistant

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ultron.assistant.data.Store
import com.ultron.assistant.service.Chime
import com.ultron.assistant.service.Focus
import com.ultron.assistant.service.HandsFreeService
import com.ultron.assistant.service.VoiceRouter

/** adb shell am broadcast -n com.ultron.assistant/.DebugCommandReceiver --es text "open settings"
 *  Logs the sentence ULTRON would say back under the tag UltronDebug. Special texts:
 *    __chime__                      plays the chime
 *    __focus_hold__ / __focus_release__ / __focus_quiet__   what a hands-free turn does to other apps' music
 *    __handsfree_on__               turns the hands-free setting on (open the app to start listening)
 *    __partial__ words / __heard__ words   feeds words to the running hands-free service as if it heard them */
class DebugCommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val text = intent.getStringExtra("text") ?: return
        val app = context.applicationContext
        val music = { app.getSystemService(AudioManager::class.java).isMusicActive }
        fun later(what: String) = Handler(Looper.getMainLooper()).postDelayed({ Log.i("UltronDebug", "$what: music playing=${music()}") }, 1500)
        when {
            text == "__chime__" -> Chime.play(Chime.Kind.READY) { Log.i("UltronDebug", "chime finished") }
            text == "__focus_hold__" -> { Log.i("UltronDebug", "before hold: music playing=${music()}"); focus(app).hold(); later("after hold") }
            text == "__focus_release__" -> { focus(app).release(); later("after release") }
            text == "__focus_quiet__" -> { focus(app).releaseQuiet(); later("after quiet release") }
            text == "__handsfree_on__" -> { Store(app).handsFree = true; Log.i("UltronDebug", "hands-free setting on") }
            text.startsWith("__partial__") || text.startsWith("__heard__") -> app.startService(
                Intent(app, HandsFreeService::class.java).setAction(HandsFreeService.ACTION_DEBUG_HEAR)
                    .putExtra("text", text.substringAfter("__ ").trim()).putExtra("final", text.startsWith("__heard__")),
            )
            else -> VoiceRouter(app) { later -> Log.i("UltronDebug", "later=[$later]") }.handle(text) { r -> Log.i("UltronDebug", "said=[${r.text}] stop=${r.stop} listenAgain=${r.listenAgain} quiet=${r.quiet}") }
        }
    }

    private companion object {
        var held: Focus? = null
        fun focus(context: Context) = held ?: Focus(context).also { held = it }
    }
}
