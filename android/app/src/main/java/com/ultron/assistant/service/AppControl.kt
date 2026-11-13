package com.ultron.assistant.service

import android.app.PendingIntent
import android.app.SearchManager
import android.content.Context
import android.content.Intent
import android.content.ComponentName
import android.content.IntentFilter
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.media.MediaMetadata
import android.media.browse.MediaBrowser
import android.media.session.MediaController
import android.media.session.PlaybackState
import android.os.SystemClock
import android.os.BatteryManager
import android.os.Bundle
import android.provider.AlarmClock
import android.util.Log
import com.ultron.assistant.core.Clocks
import com.ultron.assistant.core.Contact
import com.ultron.assistant.core.MusicQuery
import com.ultron.assistant.core.SpokenTime
import com.ultron.assistant.data.LocalReminders
import java.time.ZonedDateTime
import android.net.Uri
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.View
import android.view.WindowManager
import com.ultron.assistant.core.MusicApps
import com.ultron.assistant.core.MusicMatch
import android.provider.Settings
import android.view.KeyEvent
import androidx.core.app.NotificationCompat
import android.app.NotificationManager
import com.ultron.assistant.R
import com.ultron.assistant.core.AppCommand
import com.ultron.assistant.core.AppMatch
import com.ultron.assistant.core.MediaKey
import java.text.DateFormat
import java.util.Date

/** Does what a spoken [AppCommand] says, using ordinary Android actions (open an app, media keys,
 *  volume, a search intent). Returns the sentence to say back.
 *
 *  Android blocks apps from opening other apps from the background. When the operator has allowed
 *  "Display over other apps" the app opens at once; otherwise a notification appears and one tap opens it. */
class AppControl(
    private val context: Context,
    /** Called later (main thread) with something worth saying once a slow action has failed. */
    private val onLater: (String) -> Unit = {},
    private val preferredMusic: () -> String = { "" },
) {
    private val main = Handler(Looper.getMainLooper())
    private val pm = context.packageManager

    /** label -> package of every app with a launcher icon. */
    private fun launchable(): Map<String, String> =
        pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .associate { it.loadLabel(pm).toString() to it.activityInfo.packageName }

    fun run(cmd: AppCommand): String = when (cmd) {
        is AppCommand.OpenApp -> openApp(cmd.name)
        is AppCommand.PlayMusic -> play(cmd)
        is AppCommand.Media -> media(cmd.key)
        is AppCommand.Volume -> volume(cmd.up)
        is AppCommand.Navigate -> start(Intent(Intent.ACTION_VIEW, Uri.parse("google.navigation:q=" + Uri.encode(cmd.place))), "directions to ${cmd.place}", "Starting directions to ${cmd.place}.")
        is AppCommand.WebSearch -> start(Intent(Intent.ACTION_WEB_SEARCH).putExtra(SearchManager.QUERY, cmd.query), "a search for ${cmd.query}", "Searching for ${cmd.query}.")
        is AppCommand.Timer -> timer(cmd.seconds)
        is AppCommand.Alarm -> alarm(cmd)
        is AppCommand.Flashlight -> torch(cmd.on)
        AppCommand.Battery -> battery()
        AppCommand.Camera -> start(Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA), "the camera", "Opening the camera.")
        is AppCommand.TextMessage -> "I need to know who that is for."
        AppCommand.TellTime -> "It is ${DateFormat.getTimeInstance(DateFormat.SHORT).format(Date())} on ${DateFormat.getDateInstance(DateFormat.FULL).format(Date())}."
        AppCommand.StopListening -> "Hands-free is off."
    }

    private fun openApp(name: String): String {
        val apps = launchable()
        val label = AppMatch.best(name, apps.keys.toList()) ?: return "I could not find an app called $name."
        val intent = pm.getLaunchIntentForPackage(apps.getValue(label)) ?: return "I cannot open $label."
        return start(intent, label, "Opening $label.")
    }

    // ------------------------------------------------------------------ music
    /** Asks the music app to play [cmd] and answers at once; the request itself carries on in [PlayRequest]. */
    private fun play(cmd: AppCommand.PlayMusic): String {
        val apps = launchable()
        var pkg: String? = null
        var label = ""
        var asked = cmd.query
        if (cmd.app != null) {
            val named = AppMatch.best(cmd.app, apps.keys.toList())
            if (named != null) {
                pkg = apps.getValue(named); label = named
            } else {
                // Not an app on this phone. A music app that is not installed: say so. Anything else was
                // part of the request itself ("songs from the nineties").
                AppMatch.best(cmd.app, MusicApps.KNOWN.map { it.first })?.let { return "$it is not installed on this phone." }
                asked = "${cmd.query} ${cmd.app}"
            }
        }
        if (pkg == null) {
            pkg = MusicApps.choose(preferredMusic(), apps.values.toSet()) ?: return "No music app on this phone can play that by voice."
            label = apps.entries.firstOrNull { it.value == pkg }?.key ?: "your music app"
        }
        val query = MusicQuery.parse(asked)
        PlayRequest(pkg, label, query).start()
        return "Asking $label to play ${query.text}."
    }

    private data class Track(val title: String, val artist: String?, val album: String?) {
        val spoken get() = if (artist.isNullOrBlank()) title else "$title by $artist"
    }

    /** One "play ..." request. Apps differ in what they honour, so the routes are tried in turn: the app's
     *  own media service (what Android Auto uses; no screen needed), Android's "play from search" request
     *  (what the Google assistant uses), then the play key.
     *
     *  Where the app's media service lets ULTRON in, it also shows what is playing, so the song is checked
     *  against the request: the wrong song moves on to the next route, and if it is still wrong the operator
     *  is told what started and the app's search is opened. Apps that refuse the connection are only
     *  checked for "is any music playing". Everything is logged under the tag UltronMusic. */
    private inner class PlayRequest(private val pkg: String, private val label: String, private val q: MusicQuery) {
        private val audio = context.getSystemService(AudioManager::class.java)
        private val wasPlaying = audio.isMusicActive
        private val check = MusicMatch.checkable(q)
        private val routes = ArrayList<Pair<String, () -> Unit>>()
        private var browser: MediaBrowser? = null
        private var controller: MediaController? = null
        private var begun = false

        fun start() {
            val service = browserService(pkg)
            if (service == null) { begin(); return }
            val b = MediaBrowser(context, service, object : MediaBrowser.ConnectionCallback() {
                override fun onConnected() {
                    controller = runCatching { MediaController(context, browser!!.sessionToken) }.getOrNull()
                    begin()
                }
                override fun onConnectionFailed() { Log.i(TAG, "$pkg's media service refused the connection"); begin() }
                override fun onConnectionSuspended() = Unit
            }, null)
            browser = b
            runCatching { b.connect() }.onFailure { begin() }
            main.postDelayed({ begin() }, 4000) // an app that never answers must not hold the request up
        }

        private fun begin() {
            if (begun) return
            begun = true
            controller?.let { c -> routes += "media service" to { c.transportControls.playFromSearch(q.text, Bundle().putFocus(q)) } }
            routes += "play request" to { startFromBackgroundOrTap(playIntent(pkg, q), label, "") }
            routes += "play key" to { media(MediaKey.PLAY) }
            attempt(0)
        }

        private fun playing(): Track? {
            val c = controller ?: return null
            if (c.playbackState?.state != PlaybackState.STATE_PLAYING) return null
            val m = c.metadata ?: return null
            val title = m.getString(MediaMetadata.METADATA_KEY_TITLE) ?: m.getString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE) ?: return null
            return Track(title, m.getString(MediaMetadata.METADATA_KEY_ARTIST) ?: m.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST), m.getString(MediaMetadata.METADATA_KEY_ALBUM))
        }

        private fun right(t: Track) = !check || MusicMatch.fits(q, t.title, t.artist, t.album)

        private fun attempt(n: Int) {
            val now = runCatching { playing() }.getOrNull()
            // The play key only helps when nothing started; it cannot change the song.
            if (n >= routes.size || (now != null && routes[n].first == "play key")) { giveUp(now); return }
            Log.i(TAG, "route $n (${routes[n].first}) for $label: “${q.text}”")
            runCatching { routes[n].second() }.onFailure { Log.w(TAG, "route $n failed: ${it.message}") }
            if (controller == null) {
                // The app does not show what it plays: all that can be asked is whether music started.
                main.postDelayed({ if (wasPlaying || audio.isMusicActive) finish("something is playing after route $n") else attempt(n + 1) }, 6000)
            } else watch(n, SystemClock.uptimeMillis() + 8000, now, 0)
        }

        /** Looks once a second at what the app is playing, until it is the right song or time is up. The
         *  song from [before] may only be the old one playing on while the app searches, so it gets the
         *  whole time; a new song that is not the one asked for gets three seconds ([wrongFor] counts them). */
        private fun watch(n: Int, deadline: Long, before: Track?, wrongFor: Int) {
            main.postDelayed({
                val now = runCatching { playing() }.getOrNull()
                val wrong = if (now != null && now != before && !right(now)) wrongFor + 1 else 0
                when {
                    now != null && right(now) -> finish("playing “${now.title}” by ${now.artist} after route $n")
                    wrong < 3 && SystemClock.uptimeMillis() < deadline -> watch(n, deadline, before, wrong)
                    else -> {
                        Log.i(TAG, if (now == null) "nothing playing after route $n" else "route $n started “${now.title}” by ${now.artist}, not “${q.text}”")
                        attempt(n + 1)
                    }
                }
            }, 1000)
        }

        private fun giveUp(now: Track?) {
            startFromBackgroundOrTap(searchIntent(pkg, q), label, "")
            if (now != null) {
                finish("still playing “${now.title}” by ${now.artist}, not “${q.text}”")
                onLater("$label is playing ${now.spoken}, not ${q.text}. I opened its search so you can pick the song.")
            } else {
                finish("no route made $label play “${q.text}”")
                onLater("$label did not start ${q.text}, so I opened its search. Tap the song to play it.")
            }
        }

        private fun finish(log: String) {
            Log.i(TAG, "$label, asked “${q.text}”: $log")
            val b = browser ?: return
            browser = null; controller = null
            main.postDelayed({ runCatching { b.disconnect() } }, 2000)
        }
    }

    private fun playIntent(pkg: String, q: MusicQuery): Intent {
        val i = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).setPackage(pkg).putExtra(SearchManager.QUERY, q.text)
        if (q.title != null) {
            i.putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/audio").putExtra(MediaStore.EXTRA_MEDIA_TITLE, q.title).putExtra(MediaStore.EXTRA_MEDIA_ARTIST, q.artist)
        } else i.putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
        return i
    }

    private fun searchIntent(pkg: String, q: MusicQuery) =
        Intent(MediaStore.INTENT_ACTION_MEDIA_SEARCH).setPackage(pkg).putExtra(SearchManager.QUERY, q.text)

    private fun browserService(pkg: String): ComponentName? =
        pm.queryIntentServices(Intent("android.media.browse.MediaBrowserService").setPackage(pkg), 0)
            .firstOrNull { it.serviceInfo?.exported == true }?.serviceInfo?.let { ComponentName(it.packageName, it.name) }

    private fun Bundle.putFocus(q: MusicQuery): Bundle {
        putString(SearchManager.QUERY, q.text)
        if (q.title != null) {
            putString(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/audio"); putString(MediaStore.EXTRA_MEDIA_TITLE, q.title); putString(MediaStore.EXTRA_MEDIA_ARTIST, q.artist)
        } else putString(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
        return this
    }

    /** Starts [intent] from the background when allowed; otherwise a notification asks for one tap. */
    private fun startFromBackgroundOrTap(intent: Intent, what: String, said: String) { start(intent, what, said) }

    // ------------------------------------------------------------------ timers, alarms and the rest
    private fun timer(seconds: Long): String {
        val said = "Timer set for ${SpokenTime.duration(seconds)}."
        if (seconds in 1..86_400) {
            val i = Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, seconds.toInt())
                .putExtra(AlarmClock.EXTRA_MESSAGE, "ULTRON timer").putExtra(AlarmClock.EXTRA_SKIP_UI, true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (backgroundStart(i)) return said
        }
        LocalReminders(context).add("Timer finished: ${SpokenTime.duration(seconds)}", System.currentTimeMillis() + seconds * 1000)
        return "$said I will notify you."
    }

    private fun alarm(cmd: AppCommand.Alarm): String {
        val now = ZonedDateTime.now()
        val (h, m) = Clocks.next(cmd.hour, cmd.minute, cmd.ambiguous, now)
        val said = "Alarm set for ${SpokenTime.clock(h, m)}."
        val i = Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, h).putExtra(AlarmClock.EXTRA_MINUTES, m)
            .putExtra(AlarmClock.EXTRA_MESSAGE, "ULTRON").putExtra(AlarmClock.EXTRA_SKIP_UI, true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (backgroundStart(i)) return said
        var due = now.withHour(h).withMinute(m).withSecond(0).withNano(0)
        if (!due.isAfter(now)) due = due.plusDays(1)
        LocalReminders(context).add("Alarm: ${SpokenTime.clock(h, m)}", due.toInstant().toEpochMilli())
        return "$said I will notify you."
    }

    /** True if [intent] was handed to its app. Only when "Display over other apps" is allowed and an app answers it. */
    private fun backgroundStart(intent: Intent): Boolean =
        Settings.canDrawOverlays(context) && intent.resolveActivity(pm) != null && startFromBackground(intent)

    private fun torch(on: Boolean): String = try {
        val cm = context.getSystemService(CameraManager::class.java)
        val id = cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
        if (id == null) "This phone has no flashlight." else { cm.setTorchMode(id, on); if (on) "Flashlight on." else "Flashlight off." }
    } catch (e: Exception) {
        "I could not reach the flashlight."
    }

    private fun battery(): String {
        val b = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return "I could not read the battery."
        val level = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = b.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        if (level < 0) return "I could not read the battery."
        val charging = b.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0
        return "Battery is at ${level * 100 / scale} percent${if (charging) " and charging" else ""}."
    }

    /** Opens the messaging app with the text filled in. It is sent by the operator's tap, never by ULTRON. */
    fun compose(to: Contact, body: String): String {
        val number = to.number.filter { it.isDigit() || it == '+' }
        return start(
            Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$number")).putExtra("sms_body", body),
            "a message to ${to.name}", "I opened a message to ${to.name} with your text. Tap send.",
        )
    }

    private fun media(key: MediaKey): String {
        val code = when (key) {
            MediaKey.PLAY -> KeyEvent.KEYCODE_MEDIA_PLAY
            MediaKey.PAUSE -> KeyEvent.KEYCODE_MEDIA_PAUSE
            MediaKey.NEXT -> KeyEvent.KEYCODE_MEDIA_NEXT
            MediaKey.PREVIOUS -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
        }
        val audio = context.getSystemService(AudioManager::class.java)
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
        return when (key) {
            MediaKey.PLAY -> "Playing."
            MediaKey.PAUSE -> "Paused."
            MediaKey.NEXT -> "Next."
            MediaKey.PREVIOUS -> "Previous."
        }
    }

    private fun volume(up: Boolean): String {
        context.getSystemService(AudioManager::class.java)
            .adjustStreamVolume(AudioManager.STREAM_MUSIC, if (up) AudioManager.ADJUST_RAISE else AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
        return if (up) "Louder." else "Quieter."
    }

    /** Opens [intent] and returns [said]; when Android will not let a background app do that, a
     *  notification asks for one tap instead and the answer says so. */
    private fun start(intent: Intent, what: String, said: String): String {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Settings.canDrawOverlays(context)) {
            return if (startFromBackground(intent)) said else "I could not start $what."
        }
        if (!Notifier.canPost(context)) return "Android will not let me open $what. Allow “Display over other apps” in ULTRON settings."
        val tap = PendingIntent.getActivity(context, what.hashCode(), intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        context.getSystemService(NotificationManager::class.java).notify(
            3000 + (what.hashCode() and 0xFFFF),
            NotificationCompat.Builder(context, Notifier.ALERT_CHANNEL).setSmallIcon(R.drawable.ic_ultron)
                .setContentTitle("Tap to open $what").setContentText("To open apps by voice with no tap, allow “Display over other apps” for ULTRON.")
                .setContentIntent(tap).setAutoCancel(true).setTimeoutAfter(120_000).build(),
        )
        return "Tap the notification to open $what. To skip the tap, allow Display over other apps in ULTRON settings."
    }

    /** Android 15 and later only let an app with the overlay permission start an activity from the
     *  background while one of its overlay windows is on screen, so a one-pixel invisible window is
     *  shown for the moment of the launch. It cannot be seen or touched. */
    private fun startFromBackground(intent: Intent): Boolean {
        val wm = context.getSystemService(WindowManager::class.java)
        val dot = View(context)
        val shown = try {
            wm.addView(
                dot,
                WindowManager.LayoutParams(
                    1, 1, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                    PixelFormat.TRANSLUCENT,
                ),
            )
            true
        } catch (e: RuntimeException) {
            false
        }
        val ok = try { context.startActivity(intent); true } catch (e: RuntimeException) { false }
        if (shown) Handler(Looper.getMainLooper()).postDelayed({ runCatching { wm.removeView(dot) } }, 2500)
        return ok
    }
}

private const val TAG = "UltronMusic"
