package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BriefingTest {
    private val sms = Note(Source.SMS, "Asha", "see you at 5", 100)
    private val wa = Note(Source.WHATSAPP, "Kiran", "ping", 110)
    private val mail = Note(Source.EMAIL, "Priya", "Lunch?", 120)
    private val team = Note(Source.TEAMS, "Dev", "standup moved", 130)
    private val all = listOf(sms, wa, mail, team)

    @Test fun missedTellsEverythingNewAndMarksEverythingTold() {
        val a = Briefing.answer(ReadCommand.Missed, all, emptyMap(), hour = 9)
        assertEquals(Digest.missed(all), a.text)
        assertEquals(Source.entries.toSet(), a.told)
    }

    @Test fun whatWasToldIsNotRepeated() {
        val seen = Source.entries.associateWith { 200L }
        assertEquals("Nothing new.", Briefing.answer(ReadCommand.Missed, all, seen, 9).text)
        val fresh = all + Note(Source.SMS, "Rahul", "call me", 300)
        assertEquals("You missed 1 text message from Rahul.", Briefing.answer(ReadCommand.Missed, fresh, seen, 9).text)
    }

    @Test fun eachKindOfQuestionOnlyMarksItsOwnKind() {
        assertEquals(setOf(Source.SMS, Source.WHATSAPP), Briefing.answer(ReadCommand.Messages, all, emptyMap(), 9).told)
        assertEquals(setOf(Source.EMAIL), Briefing.answer(ReadCommand.Emails, all, emptyMap(), 9).told)
        assertEquals(setOf(Source.TEAMS), Briefing.answer(ReadCommand.Team, all, emptyMap(), 9).told)
        // asking about messages does not use up the email
        val seen = mapOf(Source.SMS to 1000L, Source.WHATSAPP to 1000L)
        assertTrue(Briefing.answer(ReadCommand.Emails, all, seen, 9).text.contains("Priya"))
    }

    @Test fun askingAboutOnePersonReadsEverythingFromThemAndMarksNothing() {
        val seen = Source.entries.associateWith { 1000L }
        val a = Briefing.answer(ReadCommand.From("asha"), all, seen, 9)
        assertEquals("Asha says: see you at 5", a.text)
        assertTrue(a.told.isEmpty())
    }

    @Test fun theMorningSummaryGreets() {
        val a = Briefing.answer(ReadCommand.Morning, all, emptyMap(), 7)
        assertTrue(a.text, a.text.startsWith("Good morning. You missed"))
        assertEquals(Source.entries.toSet(), a.told)
    }

    @Test fun clearAnswersWithoutNotesOrMarks() {
        val a = Briefing.answer(ReadCommand.Clear, all, emptyMap(), 9)
        assertEquals("Cleared everything I had read.", a.text)
        assertTrue(a.told.isEmpty())
    }
}

class MorningTriggerTest {
    private fun fire(pkg: String = "com.google.android.deskclock", category: String? = "alarm", hour: Int = 7, today: String = "2026-11-15", done: String = "", on: Boolean = true) =
        MorningTrigger.shouldFire(on, pkg, category, hour, today, done)

    @Test fun theClockAppsAlarmEndingInTheMorningFiresOncePerDay() {
        assertTrue(fire())
        assertTrue(fire(pkg = "com.android.deskclock"))
        assertTrue(fire(pkg = "com.sec.android.app.clockpackage"))
        assertFalse(fire(done = "2026-11-15"))
        assertTrue(fire(done = "2026-11-14"))
    }

    @Test fun otherNotificationsAndOtherHoursDoNot() {
        assertFalse(fire(category = "msg"))
        assertFalse(fire(category = null))
        assertFalse(fire(pkg = "com.whatsapp"))
        assertFalse(fire(hour = 3))
        assertFalse(fire(hour = 12))
        assertFalse(fire(on = false))
    }
}

class NoteSenderTest {
    @Test fun messageCountsAreStrippedFromTheSenderName() {
        assertEquals("Family group", NoteLog.sender("Family group (3 messages)"))
        assertEquals("Asha", NoteLog.sender("  Asha  "))
        assertEquals("Asha", NoteLog.sender("Asha (2 new messages)"))
    }
}
