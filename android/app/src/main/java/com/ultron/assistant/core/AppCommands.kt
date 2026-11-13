package com.ultron.assistant.core

enum class MediaKey { PLAY, PAUSE, NEXT, PREVIOUS }

/** Things ULTRON does to the phone itself, by voice. Each one is a plain, visible Android action. */
sealed interface AppCommand {
    data class OpenApp(val name: String) : AppCommand
    /** [app] is what followed "on"/"in"/"from"; it may turn out to be part of the title ("songs from the nineties"). */
    data class PlayMusic(val query: String, val app: String?) : AppCommand
    data class Media(val key: MediaKey) : AppCommand
    data class Volume(val up: Boolean) : AppCommand
    data class Navigate(val place: String) : AppCommand
    data class WebSearch(val query: String) : AppCommand
    /** [seconds] is 0 when no length was heard. */
    data class Timer(val seconds: Long) : AppCommand
    /** [hour] is -1 when no time was heard; [ambiguous] means "6" with no am/pm. */
    data class Alarm(val hour: Int, val minute: Int, val ambiguous: Boolean) : AppCommand
    data class Flashlight(val on: Boolean) : AppCommand
    data class TextMessage(val to: String, val body: String) : AppCommand
    data object Battery : AppCommand
    data object Camera : AppCommand
    data object TellTime : AppCommand
    data object StopListening : AppCommand
}

object AppCommands {
    private const val FILLER = "(?:(?:please|ultron|hey|can you|could you|would you)[\\s,:]+)*"
    private fun rx(body: String) = Regex("^\\s*$FILLER$body\\s*[.?!]*\\s*$", RegexOption.IGNORE_CASE)

    private val open = rx("(?:open|launch|start|run)\\s+(?:the\\s+)?(.{1,40}?)(?:\\s+app)?")
    // The ways people ask for a song: "play X", "play me the song X", "I want to listen to X". ("Hear" and a bare
    // "listen to" are left out on purpose: "I want to hear a joke" is a question for the computer.)
    private const val PLAY = "(?:i\\s+(?:want|wanna|would like)\\s+to\\s+(?:play|listen to)|play|put on)"
    private const val SONG = "(?:(?:me|us)\\s+)?(?:(?:the\\s+|a\\s+)?(?:song|track)\\s+(?:called\\s+|named\\s+)?)?"
    private const val APP = "(?:the\\s+)?(.{1,30}?)(?:\\s+app)?"
    // Greedy title: the app is what follows the LAST "on"/"in"/"from" ("play dance on the floor in apple music").
    private val playOn = rx("$PLAY\\s+$SONG(.{1,80})\\s+(?:on|in|using|with|from)\\s+$APP")
    // "open Apple Music and play X", "on Spotify play X": the app comes first.
    private val openAndPlay = rx("(?:open|launch|start|run|go to)\\s+$APP\\s+(?:and then|and|then|to)\\s+$PLAY\\s+$SONG(.{1,80}?)")
    private val onAppPlay = rx("(?:on|in|using|from)\\s+$APP[\\s,]+$PLAY\\s+$SONG(.{1,80}?)")
    private val play = rx("$PLAY\\s+$SONG(.{1,80}?)")
    private val resume = rx("(?:play|resume|continue)(?:\\s+(?:the\\s+|some\\s+|my\\s+)?(?:music|songs?|playback|it|something|anything))?")
    private val pause = rx("(?:pause|stop)(?:\\s+(?:the\\s+)?(?:music|song|playback|it|playing))?")
    private val next = rx("(?:next|skip)(?:\\s+(?:the\\s+)?(?:song|track|one))?")
    private val previous = rx("(?:previous|go back)(?:\\s+(?:the\\s+)?(?:song|track|one))?")
    private val volUp = rx("(?:volume up|turn it up|louder|increase (?:the )?volume|turn up (?:the )?volume)")
    private val volDown = rx("(?:volume down|turn it down|quieter|lower|decrease (?:the )?volume|turn down (?:the )?volume)")
    private val navigate = rx("(?:navigate to|directions to|take me to|route to|how do i get to)\\s+(.{1,80}?)")
    private val search = rx("(?:search(?: the web)?(?: for)?|google|look up)\\s+(.{1,100}?)")
    private val time = rx("(?:what(?:'s| is) the time|what time is it|what(?:'s| is) the date|what day is it)")
    private val timerCmd = rx("(?:(?:set|start|create|make|put|run|begin)\\s+)?(?:(?:a|an|the|my)\\s+)?(?:[\\w-]{1,12}\\s+){0,2}timer\\b.{0,40}?")
    private val timerVerb = Regex("^\\s*(?:(?:please|ultron|hey|can you|could you|would you)[\\s,:]+)*(?:set|start|create|make|put|run|begin)\\b", RegexOption.IGNORE_CASE)
    private val alarmCmd = rx("(?:(?:set|create|make|put)\\s+(?:an?\\s+)?alarm|alarm)(?:\\s+(?:for|at))?\\s*(.{0,30}?)")
    private val wakeMe = rx("wake me(?: up)?\\s+(?:at|by|around)\\s+(.{1,30}?)")
    private val clockTime = Regex("(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?\\b|\\b(noon|midnight)\\b", RegexOption.IGNORE_CASE)
    private val torch = rx("(?:(?:turn|switch)\\s+(on|off)\\s+(?:the\\s+)?|(?:the\\s+)?)(?:flash\\s?light|torch)(?:\\s+(on|off))?")
    private val battery = rx("(?:what(?:'s| is) (?:the |my )?battery(?: level| percentage| percent)?|how much battery(?: do i have| is left| left)?|battery (?:level|percentage|status|left))")
    private val camera = rx("(?:take (?:a )?(?:photo|picture|selfie|pic)|open (?:the )?camera)")
    private val textMsg = rx("(?:text|message|sms|send (?:a )?(?:text|message|sms)(?: message)? to)\\s+(.{1,40}?)\\s+(?:saying|that says|and say|to say|that|say)\\s+(.{1,200}?)")
    private val stopListening = rx("(?:stop listening|go to sleep|hands[- ]?free off|that'?s all|goodbye|bye)")

    fun parse(text: String): AppCommand? {
        stopListening.matchEntire(text)?.let { return AppCommand.StopListening }
        if (timerCmd.matchEntire(text) != null) {
            val secs = Durations.seconds(text)
            if (secs > 0 || timerVerb.containsMatchIn(text)) return AppCommand.Timer(secs) // "the timer is broken" is not a command
        }
        (alarmCmd.matchEntire(text) ?: wakeMe.matchEntire(text))?.let { return alarm(it.groupValues[1]) }
        torch.matchEntire(text)?.let { m -> return AppCommand.Flashlight(!(m.groupValues[1].equals("off", true) || m.groupValues[2].equals("off", true))) }
        battery.matchEntire(text)?.let { return AppCommand.Battery }
        camera.matchEntire(text)?.let { return AppCommand.Camera }
        textMsg.matchEntire(text)?.let { return AppCommand.TextMessage(clean(it.groupValues[1]), clean(it.groupValues[2])) }
        time.matchEntire(text)?.let { return AppCommand.TellTime }
        volUp.matchEntire(text)?.let { return AppCommand.Volume(true) }
        volDown.matchEntire(text)?.let { return AppCommand.Volume(false) }
        next.matchEntire(text)?.let { return AppCommand.Media(MediaKey.NEXT) }
        previous.matchEntire(text)?.let { return AppCommand.Media(MediaKey.PREVIOUS) }
        pause.matchEntire(text)?.let { return AppCommand.Media(MediaKey.PAUSE) }
        resume.matchEntire(text)?.let { return AppCommand.Media(MediaKey.PLAY) }
        (openAndPlay.matchEntire(text) ?: onAppPlay.matchEntire(text))?.let { return AppCommand.PlayMusic(clean(it.groupValues[2]), clean(it.groupValues[1])) }
        playOn.matchEntire(text)?.let { return AppCommand.PlayMusic(clean(it.groupValues[1]), clean(it.groupValues[2])) }
        play.matchEntire(text)?.let { return AppCommand.PlayMusic(clean(it.groupValues[1]), null) }
        navigate.matchEntire(text)?.let { return AppCommand.Navigate(clean(it.groupValues[1])) }
        search.matchEntire(text)?.let { return AppCommand.WebSearch(clean(it.groupValues[1])) }
        open.matchEntire(text)?.let { return AppCommand.OpenApp(clean(it.groupValues[1])) }
        return null
    }

    private fun alarm(said: String): AppCommand.Alarm {
        val m = clockTime.find(said) ?: return AppCommand.Alarm(-1, 0, false)
        val word = m.groupValues[4].lowercase()
        if (word == "noon") return AppCommand.Alarm(12, 0, false)
        if (word == "midnight") return AppCommand.Alarm(0, 0, false)
        val h = m.groupValues[1].toIntOrNull() ?: return AppCommand.Alarm(-1, 0, false)
        val min = m.groupValues[2].toIntOrNull() ?: 0
        val ap = m.groupValues[3].lowercase()
        if (min > 59 || h > 23) return AppCommand.Alarm(-1, 0, false)
        if (ap.isNotEmpty()) return if (h in 1..12) AppCommand.Alarm(h % 12 + if (ap == "pm") 12 else 0, min, false) else AppCommand.Alarm(-1, 0, false)
        return AppCommand.Alarm(h, min, h in 1..12)
    }

    private fun clean(s: String) = s.trim().replace(Regex("\\s+"), " ")
}

/** Picks the installed app a spoken name means ("youtube music", "spotify", "whatsapp"). */
object AppMatch {
    private fun norm(s: String) = s.lowercase().filter { it.isLetterOrDigit() }

    /** Exact label first, then a label that starts with / contains what was said, never a loose guess. */
    fun best(said: String, labels: List<String>): String? {
        val q = norm(said)
        if (q.length < 2) return null
        labels.firstOrNull { norm(it) == q }?.let { return it }
        val starts = labels.filter { norm(it).startsWith(q) }
        if (starts.size == 1) return starts[0]
        val contains = labels.filter { q in norm(it) || (norm(it).length >= 4 && norm(it) in q) }
        if (contains.size == 1) return contains[0]
        return null
    }
}

/** "5 minutes", "an hour", "1 hour 30 minutes", "half an hour" -> seconds (0 when no length is in the text). */
object Durations {
    private val part = Regex("\\b(\\d{1,4}|an?|half an?)\\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\\b", RegexOption.IGNORE_CASE)

    fun seconds(text: String): Long {
        var total = 0L
        for (m in part.findAll(text)) {
            val n = m.groupValues[1].lowercase()
            val unit = m.groupValues[2].lowercase()
            val per = when { unit.startsWith("hour") || unit.startsWith("hr") -> 3600.0; unit.startsWith("min") -> 60.0; else -> 1.0 }
            val count = if (n.startsWith("half")) 0.5 else n.toDoubleOrNull() ?: 1.0
            total += (count * per).toLong()
        }
        if (Regex("\\bhour and a half\\b", RegexOption.IGNORE_CASE).containsMatchIn(text)) total += 1800
        return total
    }
}

/** Which 24-hour time "6" with no am/pm means: the next one that comes round. */
object Clocks {
    fun next(hour: Int, minute: Int, ambiguous: Boolean, now: java.time.ZonedDateTime): Pair<Int, Int> {
        if (!ambiguous) return hour to minute
        val options = listOf(hour % 12, hour % 12 + 12).map { h ->
            val c = now.withHour(h).withMinute(minute).withSecond(0).withNano(0)
            h to (if (c.isAfter(now)) c else c.plusDays(1))
        }
        return options.minBy { it.second }.first to minute
    }
}
