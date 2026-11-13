package com.ultron.assistant.core

/** Music apps ULTRON knows how to ask to play something (Android's "play from search" request). */
object MusicApps {
    val KNOWN = listOf(
        "Spotify" to "com.spotify.music",
        "YouTube Music" to "com.google.android.apps.youtube.music",
        "Apple Music" to "com.apple.android.music",
        "Amazon Music" to "com.amazon.mp3",
        "JioSaavn" to "com.jio.media.jiobeats",
        "Gaana" to "com.gaana",
        "Wynk Music" to "com.bsbportal.music",
        "Deezer" to "deezer.android.app",
    )

    /** The app to play in: the one chosen in settings if it is installed, else the first known one that is. */
    fun choose(preferred: String, installed: Set<String>): String? =
        preferred.takeIf { it.isNotEmpty() && it in installed } ?: KNOWN.map { it.second }.firstOrNull { it in installed }
}

/** "kesariya by arijit singh" -> title and artist, for apps that take them separately. */
data class MusicQuery(val text: String, val title: String?, val artist: String?) {
    companion object {
        fun parse(said: String): MusicQuery {
            val t = said.trim()
            val m = Regex("^(.{1,80}?)\\s+by\\s+(.{1,60})$", RegexOption.IGNORE_CASE).matchEntire(t)
            return if (m != null) MusicQuery(t, m.groupValues[1].trim(), m.groupValues[2].trim()) else MusicQuery(t, null, null)
        }
    }
}

/** Did the app start what was asked for? Compares the words of the request with the title, artist and album
 *  the app reports. Loose on purpose: recognisers and catalogues spell names differently. */
object MusicMatch {
    private val filler = setOf("the", "a", "an", "of", "by", "from", "and", "in", "on", "to", "song", "track", "movie", "film", "version", "feat", "ft", "play")
    private val open = setOf(
        "some", "any", "songs", "music", "playlist", "playlists", "radio", "station", "mix", "hits", "best", "latest", "new", "top",
        "album", "albums", "random", "something", "anything", "favourite", "favourites", "favorite", "favorites", "my", "liked", "shuffle",
    )

    private fun words(s: String) = s.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), " ").split(' ').filter { it.isNotEmpty() }
    private fun wanted(q: MusicQuery) = words(q.title ?: q.text).filter { it !in filler }

    /** "some jazz" or "arijit singh songs" have no one right title, so there is nothing to check. */
    fun checkable(q: MusicQuery): Boolean = wanted(q).isNotEmpty() && words(q.text).none { it in open }

    fun fits(q: MusicQuery, title: String?, artist: String?, album: String?): Boolean {
        val want = wanted(q)
        if (want.isEmpty()) return true
        val by = words(artist.orEmpty())
        val work = words(listOfNotNull(title, album).joinToString(" "))
        if (want.all { has(it, by) }) return true // an artist was asked for: any of their songs will do
        val hits = want.count { has(it, work) || has(it, by) }
        return hits * 2 >= want.size && want.any { has(it, work) }
    }

    private fun has(word: String, among: List<String>) =
        among.any { near(word, it) } || (word.length >= 4 && word in among.joinToString(""))

    /** Equal, or one letter apart for longer words ("kesaria" / "kesariya"). */
    private fun near(a: String, b: String): Boolean {
        if (a == b) return true
        if (a.length < 5 || b.length < 5 || kotlin.math.abs(a.length - b.length) > 1) return false
        val (short, long) = if (a.length <= b.length) a to b else b to a
        var i = 0
        while (i < short.length && short[i] == long[i]) i++
        return if (short.length == long.length) short.substring(i + 1) == long.substring(i + 1) else short.substring(i) == long.substring(i + 1)
    }
}

/** "90 seconds" -> "a minute and a half" is overkill; this just reads a length of time aloud. */
object SpokenTime {
    fun duration(seconds: Long): String {
        val h = seconds / 3600
        val m = seconds % 3600 / 60
        val s = seconds % 60
        val parts = ArrayList<String>()
        if (h > 0) parts += if (h == 1L) "1 hour" else "$h hours"
        if (m > 0) parts += if (m == 1L) "1 minute" else "$m minutes"
        if (s > 0 && h == 0L) parts += if (s == 1L) "1 second" else "$s seconds"
        return parts.joinToString(" ").ifEmpty { "0 seconds" }
    }

    fun clock(hour: Int, minute: Int): String {
        val h12 = if (hour % 12 == 0) 12 else hour % 12
        return "$h12:${minute.toString().padStart(2, '0')} ${if (hour < 12) "AM" else "PM"}"
    }
}
