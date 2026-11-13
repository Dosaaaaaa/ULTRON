package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UrlRulesTest {
    private fun ok(url: String, plain: Boolean = false) = UrlRules.parse(url, plain) is UrlRules.Result.Ok

    @Test fun httpsHostsAreAccepted() {
        assertEquals(UrlRules.Result.Ok("https://192.168.1.20:8443"), UrlRules.parse(" https://192.168.1.20:8443/ ", false))
    }

    @Test fun plainHttpIsRefusedInReleaseAndOnlyAllowedForTheEmulatorHostInDebug() {
        assertFalse(ok("http://192.168.1.20:8000"))
        assertFalse(ok("http://192.168.1.20:8000", plain = true)) // debug still refuses real hosts
        assertTrue(ok("http://10.0.2.2:8000", plain = true))
        assertFalse(ok("http://10.0.2.2:8000", plain = false))
    }

    @Test fun oddAddressesAreRefused() {
        for (bad in listOf("", "ftp://x.test", "https://", "https://user:pw@x.test", "https://x.test/path", "https://x.test?a=1", "javascript:alert(1)", "not a url")) {
            assertFalse(bad, ok(bad))
        }
    }

    @Test fun webSocketUrlsFollowTheScheme() {
        assertEquals("wss://h:1/v1/ws/device", UrlRules.webSocketUrl("https://h:1", "/v1/ws/device"))
        assertEquals("ws://10.0.2.2:8000/v1/ws/device", UrlRules.webSocketUrl("http://10.0.2.2:8000", "/v1/ws/device"))
    }
}

class FingerprintTest {
    private val der = "certificate".toByteArray()

    @Test fun anyOfTheCommonSpellingsMatchTheCertificate() {
        val hex = Fingerprint.of(der)
        assertTrue(Fingerprint.matches(Fingerprint.pretty(hex), der))
        assertTrue(Fingerprint.matches(Fingerprint.pretty(hex).lowercase(), der))
        assertTrue(Fingerprint.matches("sha256 Fingerprint=" + Fingerprint.pretty(hex), der))
        assertTrue(Fingerprint.matches(hex.chunked(8).joinToString(" "), der))
    }

    @Test fun aDifferentOrMalformedFingerprintNeverMatches() {
        val other = Fingerprint.of("another".toByteArray())
        assertFalse(Fingerprint.matches(other, der))
        assertFalse(Fingerprint.matches("", der))
        assertFalse(Fingerprint.matches("AA:BB", der))
        assertFalse(Fingerprint.matches("ZZ".repeat(32), der))
        assertNull(Fingerprint.normalise("not hex"))
    }

    @Test fun oneChangedDigitIsEnough() {
        val hex = Fingerprint.of(der)
        val flipped = (if (hex[0] == 'A') 'B' else 'A') + hex.substring(1)
        assertFalse(Fingerprint.matches(flipped, der))
    }
}

class PushParserTest {
    @Test fun aNotificationIsParsedAndCleaned() {
        val raw = """{"type":"push","id":"i1","kind":"notify","payload":{"title":"Build\u0000 done","body":"all green"}}"""
        val item = (PushParser.parse(raw) as Incoming.Item).push as Push.Notice
        assertEquals("Build  done", item.title)
        assertEquals("all green", item.body)
    }

    @Test fun aReminderAndACallProposalAreParsed() {
        val r = (PushParser.parse("""{"type":"push","id":"r","kind":"reminder","payload":{"id":"x","text":"Stand up","due_at":"2026-11-13T09:00:00+00:00"}}""") as Incoming.Item).push
        assertTrue(r is Push.Reminder && r.text == "Stand up")
        val c = (PushParser.parse("""{"type":"push","id":"c","kind":"call_proposal","created_at":"2026-11-12T10:00:00+00:00","payload":{"request_id":"q","kind":"name","target":"Asha"}}""") as Incoming.Item).push
        assertTrue(c is Push.CallProposal && c.target == "Asha" && c.createdAtMillis == 1_794_477_600_000L)
    }

    @Test fun hugeTextIsTruncated() {
        val raw = """{"type":"push","id":"i","kind":"notify","payload":{"title":"${"t".repeat(500)}","body":"${"b".repeat(5000)}"}}"""
        val n = (PushParser.parse(raw) as Incoming.Item).push as Push.Notice
        assertEquals(80, n.title.length)
        assertEquals(400, n.body.length)
    }

    @Test fun malformedOrUnknownMessagesAreIgnoredNotCrashed() {
        for (raw in listOf("", "not json", "[]", "{}", """{"type":"push"}""", """{"type":"push","id":"a","kind":"nuke","payload":{}}""",
            """{"type":"push","id":"","kind":"notify","payload":{}}""", """{"type":"push","id":"a","kind":"call_proposal","payload":{"kind":"evil","target":"x"}}""",
            """{"type":"push","id":"a","kind":"call_proposal","payload":{"kind":"name","target":""}}""")) {
            assertEquals(raw, Incoming.Ignored, PushParser.parse(raw))
        }
        assertEquals(Incoming.Pong, PushParser.parse("""{"type":"pong"}"""))
        assertEquals(Incoming.Failure("rate_limited"), PushParser.parse("""{"type":"error","code":"rate_limited"}"""))
    }
}

class CallPolicyTest {
    @Test fun aProposalExpiresAfterFiveMinutes() {
        val t = 1_000_000_000L
        assertTrue(CallPolicy.isFresh(t, t + 60_000))
        assertTrue(CallPolicy.isFresh(t, t + 4 * 60_000)) // absolute minutes: not tied to the constant
        assertFalse(CallPolicy.isFresh(t, t + 6 * 60_000))
        assertFalse(CallPolicy.isFresh(t, t + 12 * 3_600_000)) // "call Asha" at 9:00 is not dialled at noon
        assertFalse(CallPolicy.isFresh(null, t)) // unknown age: do not dial
        assertFalse(CallPolicy.isFresh(t, t - 10 * 60_000)) // from the future: clock trouble, refuse
    }

    @Test fun onlyDigitsAndALeadingPlusReachTheDialer() {
        assertEquals("+442079460958", CallPolicy.dialable("+44 (20) 7946-0958"))
        assertEquals("5550100", CallPolicy.dialable("555 0100"))
        assertNull(CallPolicy.dialable("12"))
        assertNull(CallPolicy.dialable("1".repeat(20)))
        assertNull(CallPolicy.dialable("*#06#"))
        assertEquals("1234567", CallPolicy.dialable("123;456,7")) // separators that dialers treat as commands are dropped
    }

    private val book = listOf(
        Contact("Asha Rao", "+91 98765 43210"), Contact("Asha", "555 0100"), Contact("Ashley Kim", "555 0111"),
        Contact("Dr. Patel", "555 0122"), Contact("Asha", "5550100"),
    )

    @Test fun exactNamesWinAndDuplicatesCollapse() {
        assertEquals(listOf(Contact("Asha", "555 0100")), ContactChoice.rank("asha", book))
    }

    @Test fun partialNamesGiveEveryCandidateSoThePersonChooses() {
        assertEquals(listOf("Ashley Kim"), ContactChoice.rank("ashley", book).map { it.name })
        assertEquals(listOf("Dr. Patel"), ContactChoice.rank("patel", book).map { it.name })
        assertEquals(emptyList<Contact>(), ContactChoice.rank("zed", book))
        assertEquals(emptyList<Contact>(), ContactChoice.rank("  ", book))
    }
}

class BackoffTest {
    @Test fun delaysDoubleUpToACeilingAndResetOnSuccess() {
        val b = Backoff { 0.0 }
        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 32_000L, 60_000L, 60_000L), List(8) { b.nextMillis() })
        b.reset()
        assertEquals(1_000L, b.nextMillis())
    }

    @Test fun jitterNeverExceedsAQuarter() {
        val b = Backoff { 1.0 }
        assertEquals(1_250L, b.nextMillis())
    }

    @Test fun seenReportsEachIdOnceAndForgetsTheOldest() {
        val s = Seen(capacity = 2)
        assertTrue(s.firstTime("a")); assertFalse(s.firstTime("a"))
        assertTrue(s.firstTime("b")); assertTrue(s.firstTime("c")) // "a" is pushed out
        assertTrue(s.firstTime("a"))
    }
}
