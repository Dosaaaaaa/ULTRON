package com.ultron.assistant.core

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val H = 3_600_000L
private fun note(s: Source, who: String, text: String, at: Long) = Note(s, who, text, at)

class NoteLogTest {
    @Test fun oldNotesAreForgottenAfter48Hours() {
        val now = 100 * H
        val list = listOf(note(Source.SMS, "A", "old", now - 49 * H), note(Source.SMS, "B", "fresh", now - 47 * H))
        assertEquals(listOf("fresh"), NoteLog.prune(list, now).map { it.preview })
    }

    @Test fun theSameMessageRepostedByTheAppIsKeptOnce() {
        val a = note(Source.WHATSAPP, "Asha", "are we meeting?", 1000)
        val once = NoteLog.add(emptyList(), a)
        val again = NoteLog.add(once, a.copy(whenMs = 1000 + 60_000))
        assertEquals(1, again.size)
        val later = NoteLog.add(again, a.copy(whenMs = 1000 + 20 * 60_000)) // twenty minutes on: a new message
        assertEquals(2, later.size)
        val different = NoteLog.add(later, a.copy(preview = "never mind"))
        assertEquals(3, different.size)
    }

    @Test fun theLogIsCappedAndKeepsTheNewest() {
        var list = emptyList<Note>()
        repeat(NoteLog.MAX + 20) { list = NoteLog.add(list, note(Source.SMS, "A", "m$it", it * 1000L)) }
        assertEquals(NoteLog.MAX, list.size)
        assertEquals("m${NoteLog.MAX + 19}", list.last().preview)
    }

    @Test fun unseenMeansNewerThanTheLastTimeTheyWereTold() {
        val list = listOf(note(Source.SMS, "A", "1", 100), note(Source.SMS, "B", "2", 200))
        assertEquals(listOf("2"), NoteLog.unseen(list, 150).map { it.preview })
        assertEquals(2, NoteLog.unseen(list, 0).size)
    }

    @Test fun theLogSurvivesBeingWrittenAndRead() {
        val list = listOf(note(Source.EMAIL, "Priya", "Lunch? \"today\"", 5), note(Source.TEAMS, "Dev", "ok", 6))
        val json = NoteLog.encode(list)
        JSONArray(json) // valid JSON
        assertEquals(list, NoteLog.decode(json))
        assertEquals(emptyList<Note>(), NoteLog.decode("not json"))
        assertEquals(emptyList<Note>(), NoteLog.decode(""))
    }

    @Test fun previewsAreShortened() {
        assertEquals(NoteLog.PREVIEW_MAX, NoteLog.clip("x".repeat(500)).length)
        assertEquals("hi there", NoteLog.clip("  hi   there \n"))
    }
}

class DigestTest {
    private val now = 10 * H
    private val sms = note(Source.SMS, "Asha", "see you at 5", now - 1000)
    private val sms2 = note(Source.SMS, "Rahul", "call me", now - 900)
    private val mail = note(Source.EMAIL, "Priya", "Lunch tomorrow?", now - 800)
    private val team = note(Source.TEAMS, "Dev", "standup moved to 10:30", now - 700)

    @Test fun nothingNewIsSaidPlainly() {
        assertEquals("Nothing new.", Digest.missed(emptyList()))
    }

    @Test fun missedGivesCountsAndSendersPerKind() {
        assertEquals(
            "You missed 2 text messages from Asha and Rahul, 1 email from Priya and 1 Teams message from Dev.",
            Digest.missed(listOf(sms, sms2, mail, team)),
        )
        assertEquals("You missed 1 text message from Asha.", Digest.missed(listOf(sms)))
    }

    @Test fun manySendersAreShortened() {
        val many = listOf("A", "B", "C", "D", "E").map { note(Source.WHATSAPP, it, "hi", 1) }
        assertEquals("You missed 5 WhatsApp messages from A, B, C and 2 others.", Digest.missed(many))
    }

    @Test fun oneSenderWithSeveralMessagesIsCountedOnce() {
        val three = List(3) { note(Source.WHATSAPP, "Asha", "m$it", it.toLong()) }
        assertEquals("You missed 3 WhatsApp messages from Asha.", Digest.missed(three))
    }

    @Test fun emailsAreReadWithTheirSubject() {
        assertEquals("You have 1 email. Priya: Lunch tomorrow?", Digest.about(Source.EMAIL, listOf(mail, sms)))
        assertEquals("No new emails.", Digest.about(Source.EMAIL, listOf(sms)))
    }

    @Test fun messagesCoverTextsAndWhatsAppButNotEmail() {
        val wa = note(Source.WHATSAPP, "Kiran", "ping", now)
        val text = Digest.messages(listOf(sms, wa, mail))
        assertTrue(text, text.startsWith("You have 2 messages."))
        assertTrue(text, text.contains("Asha: see you at 5"))
        assertTrue(text, text.contains("Kiran: ping"))
        assertFalse(text, text.contains("Priya"))
        assertEquals("No new messages.", Digest.messages(listOf(mail)))
    }

    @Test fun teamStatusIsHonestAboutWhatItCanSee() {
        val text = Digest.team(listOf(team))
        assertTrue(text, text.contains("1 new Teams message"))
        assertTrue(text, text.contains("Dev: standup moved to 10:30"))
        assertTrue(text, text.contains("notifications"))
        assertTrue(Digest.team(emptyList()).startsWith("No new Teams messages."))
    }

    @Test fun readingFromOnePersonMatchesLooselyAndSaysWhenThereIsNothing() {
        assertEquals("Asha says: see you at 5", Digest.from("asha", listOf(sms, sms2, mail)))
        assertEquals("Nothing from Meera.", Digest.from("Meera", listOf(sms)))
    }

    @Test fun readingIsCappedAtFive() {
        val ten = List(10) { note(Source.SMS, "Asha", "m$it", it.toLong()) }
        val text = Digest.from("Asha", ten)
        assertEquals(5, Regex("Asha says").findAll(text).count())
        assertTrue(text, text.contains("5 more"))
        assertTrue(text, text.contains("m9") && !text.contains("m4"))
    }

    @Test fun theMorningSummaryGreetsByTheHour() {
        assertTrue(Digest.morning(listOf(sms), hour = 7).startsWith("Good morning. You missed 1 text message from Asha."))
        assertEquals("Good morning. Nothing new overnight.", Digest.morning(emptyList(), hour = 7))
        assertTrue(Digest.morning(listOf(sms), hour = 14).startsWith("Hello."))
    }

    @Test fun codesAndBankTextNeverReachTheDigestBecauseTheyAreNeverStored() {
        // Only Notes exist here, and Notes are created only from Verdict.Keep (see NoteFilterTest).
        assertNull(Source.byKey("bank"))
    }
}

class ReadCommandsTest {
    private fun p(s: String) = ReadCommands.parse(s)

    @Test fun missed() {
        for (s in listOf("what did I miss", "what have I missed", "anything new", "catch me up", "what's new", "any notifications", "did I miss anything"))
            assertEquals(s, ReadCommand.Missed, p(s))
    }

    @Test fun messages() {
        for (s in listOf("any messages", "do I have any new messages", "has anyone texted me", "did anyone message me", "have I got any texts", "who texted me", "read my messages"))
            assertEquals(s, ReadCommand.Messages, p(s))
    }

    @Test fun emails() {
        for (s in listOf("any emails", "do I have any new emails", "have I got any mail", "check my email", "read my emails"))
            assertEquals(s, ReadCommand.Emails, p(s))
    }

    @Test fun team() {
        for (s in listOf("what's my team's status", "what is my team status", "any teams messages", "any team notifications", "what is happening on teams", "check teams"))
            assertEquals(s, ReadCommand.Team, p(s))
    }

    @Test fun fromAPerson() {
        assertEquals(ReadCommand.From("Asha"), p("read the messages from Asha"))
        assertEquals(ReadCommand.From("Asha"), p("what did Asha say"))
        assertEquals(ReadCommand.From("Asha"), p("what did Asha text me"))
        assertEquals(ReadCommand.From("Asha Menon"), p("any messages from Asha Menon"))
    }

    @Test fun morningAndClear() {
        for (s in listOf("good morning", "what's the summary", "morning summary", "give me the summary", "summary"))
            assertEquals(s, ReadCommand.Morning, p(s))
        assertEquals(ReadCommand.Clear, p("clear my notifications"))
        assertEquals(ReadCommand.Clear, p("forget my messages"))
    }

    @Test fun fillerWordsAreAccepted() {
        assertEquals(ReadCommand.Missed, p("please tell me what did I miss"))
        assertEquals(ReadCommand.Emails, p("hey any emails?"))
    }

    @Test fun otherSentencesAreNotReadCommands() {
        for (s in listOf("play some jazz", "call asha", "text asha saying hello", "what time is it", "remind me to read the messages tomorrow",
            "open whatsapp", "what is the capital of france", "send an email to asha", "I missed the bus"))
            assertNull(s, p(s))
    }
}
