package com.ultron.assistant.core

import java.time.Instant
import java.time.ZoneId
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairLinkTest {
    private val fp = "B2:20:9D:9C:C1:B4:F2:BC:A3:96:5B:B5:D0:D5:DB:6A:E1:BD:A8:0F:6F:12:7D:08:0B:D6:3A:B2:1D:C5:20:E3"
    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
    private fun link(u: String = "https://192.168.0.112:8443", c: String = "CJ3F-ZM8A", f: String? = fp) =
        "ultron://pair?u=${enc(u)}&c=$c" + (f?.let { "&f=${enc(it)}" } ?: "")

    @Test fun theDesktopsOwnLinkIsAccepted() {
        val p = PairLink.parse(link(), false) as PairLink.Companion.Parsed.Ok
        assertEquals("https://192.168.0.112:8443", p.link.baseUrl)
        assertEquals("CJ3F-ZM8A", p.link.code)
        assertEquals(Fingerprint.normalise(fp), p.link.fingerprint)
    }

    @Test fun codesAreUpperCasedAndMayOmitTheDash() {
        val p = PairLink.parse(link(c = "cj3fzm8a"), false) as PairLink.Companion.Parsed.Ok
        assertEquals("CJ3FZM8A", p.link.code)
    }

    @Test fun aLinkWithoutACertificateIsRefusedForHttps() {
        assertTrue(PairLink.parse(link(f = null), false) is PairLink.Companion.Parsed.Rejected)
    }

    @Test fun plainHttpIsRefusedExceptForTheEmulatorInDebug() {
        assertTrue(PairLink.parse(link(u = "http://192.168.0.5:8000", f = null), true) is PairLink.Companion.Parsed.Rejected)
        assertTrue(PairLink.parse(link(u = "http://10.0.2.2:8000", f = null), true) is PairLink.Companion.Parsed.Ok)
        assertTrue(PairLink.parse(link(u = "http://10.0.2.2:8000", f = null), false) is PairLink.Companion.Parsed.Rejected)
    }

    @Test fun anythingOddIsRefused() {
        val bad = listOf(
            "", "hello", "https://192.168.0.5:8443", "ultron://other?u=x", "ultron://pair?", "ultron://pair?u=&c=",
            link(c = "short"), link(c = "ABCD-EFGH-IJKL"), link(c = "AB CD-EFGH"), link(f = "AA:BB"),
            link(u = "https://user:pw@192.168.0.5:8443"), link(u = "javascript:alert(1)"),
            link() + "&u=https%3A%2F%2Fevil.example", // a repeated parameter
            "ultron://pair?" + "a=".repeat(1000),
        )
        for (b in bad) assertTrue(b, PairLink.parse(b, false) is PairLink.Companion.Parsed.Rejected)
    }
}

class DisplayTest {
    private val zone = ZoneId.of("Asia/Kolkata")
    private val now = Instant.parse("2026-11-12T12:00:00Z") // 17:30 IST, Thursday
    private fun at(iso: String) = Display.when_(iso, now, zone, Locale.ENGLISH)

    @Test fun reminderTimesAreShownInTheOwnersZoneWithTheDay() {
        assertEquals("Today 17:58", at("2026-11-12T12:28:00+00:00"))
        assertEquals("Tomorrow 09:00", at("2026-11-13T03:30:00+00:00"))
        assertEquals("Yesterday 17:30", at("2026-11-11T12:00:00+00:00"))
        assertEquals("Mon 16 Nov 08:30", at("2026-11-16T03:00:00Z"))
        assertEquals("not a time", at("not a time"))
    }

    @Test fun distanceIsPlainWords() {
        fun d(iso: String) = Display.distance(iso, now)
        assertEquals("due now", d("2026-11-12T12:00:20+00:00"))
        assertEquals("in 5 min", d("2026-11-12T12:05:00+00:00"))
        assertEquals("in 2 h", d("2026-11-12T14:00:00+00:00"))
        assertEquals("in 3 days", d("2026-11-15T12:00:00+00:00"))
        assertEquals("10 min ago", d("2026-11-12T11:50:00+00:00"))
        assertEquals("just now", d("2026-11-12T11:59:40+00:00"))
        assertEquals("", d("junk"))
    }
}

class ChatParserTest {
    @Test fun aPlainReplyHasNoActions() {
        val r = ChatParser.reply(JSONObject("""{"reply":"Hello.","conversation_id":"c1","actions":[]}"""))
        assertEquals("Hello.", r.text); assertEquals("c1", r.conversationId); assertTrue(r.actions.isEmpty())
    }

    @Test fun callAndReminderActionsBecomeShortSentences() {
        val r = ChatParser.reply(JSONObject("""{"reply":"ok","actions":[
            {"type":"call","status":"awaiting_confirmation","target":"Asha"},
            {"type":"reminder","status":"created","due_local":"17:58 today (in 1 minute)"},
            {"type":"reminder","status":"unclear","why":"I did not catch when."},
            {"type":"call","status":"no_phone"},{"type":"mystery","status":"x"}]}"""))
        assertEquals(listOf(
            "A call confirmation was sent to this phone.",
            "Reminder set for 17:58 today (in 1 minute).",
            "No reminder was set: I did not catch when.",
            "No phone is allowed to make calls, so nothing happened.",
        ), r.actions.map { it.summary })
        assertNull(r.conversationId)
    }

    @Test fun remindersAreParsedAndBounded() {
        val arr = JSONArray()
        for (i in 0 until 300) arr.put(JSONObject().put("id", "r$i").put("text", "t$i").put("due_at", "2026-11-13T09:00:00+00:00").put("status", "pending"))
        arr.put(JSONObject().put("text", "no id"))
        val items = ChatParser.reminders(arr)
        assertEquals(200, items.size)
        assertEquals("r0", items.first().id)
    }
}
