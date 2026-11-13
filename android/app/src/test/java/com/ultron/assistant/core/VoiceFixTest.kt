package com.ultron.assistant.core

import java.time.ZoneOffset
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Sentences are what the small offline recogniser actually wrote for synthetic speech (scratch eval, 6 voices). */
class VoiceFixTest {
    private val now = ZonedDateTime.of(2026, 11, 13, 15, 0, 0, 0, ZoneOffset.ofHoursMinutes(5, 30))
    private fun cmd(heard: String) = Commands.parse(Spoken.fix(heard), now, true)

    @Test fun inBecomesAndIsUndone() {
        assertEquals(Command.Remind("drink water", now.plusMinutes(10)), cmd("remind me and ten minutes to drink water"))
        assertEquals(Command.Remind("stretch", now.plusMinutes(30)), cmd("reminds me and thirty minutes to stretch"))
    }

    @Test fun spokenClockTimesBecomeDigits() {
        assertEquals("remind me at 5:30pm to call mom", Spoken.fix("remind me at five thirty pm to call mom"))
        assertEquals("remind me at 5:30pm to call mom", Spoken.fix("remind me at five thirty p m to call mom"))
        assertEquals("remind me tomorrow at 9am to pay the electricity bill", Spoken.fix("remind me tomorrow at nine a m to pay the electricity bill"))
        assertEquals("set an alarm for 6:30am", Spoken.fix("set an alarm for six thirty a m"))
        assertEquals("alarm for 6:05", Spoken.fix("alarm for six oh five"))
        assertEquals("at 5 to leave", Spoken.fix("at five to leave"))
        assertEquals("at 5:30", Spoken.fix("at half past five"))
        assertEquals("at 5:45", Spoken.fix("at quarter to six"))
        assertEquals("at 6am", Spoken.fix("at six in the morning"))
        assertEquals("at 7pm", Spoken.fix("at seven in the evening"))
    }

    @Test fun reminderWithSpokenTimeIsUnderstood() {
        val r = cmd("remind me at five thirty pm to call mom") as Command.Remind
        assertEquals("call mom", r.text)
        assertEquals(17, r.due.hour); assertEquals(30, r.due.minute)
        val t = cmd("remind me tomorrow at nine a m to pay the electricity bill") as Command.Remind
        assertEquals(14, t.due.dayOfMonth); assertEquals(9, t.due.hour)
    }

    @Test fun numberWordsStayWordsWhereTheyAreNotATime() {
        assertEquals("play one direction", Spoken.fix("play one direction"))
        assertEquals("play one more song", Spoken.fix("play one more song"))
        assertEquals("call me a taxi", Spoken.fix("call me a taxi"))
    }

    @Test fun spokenPhoneNumbers() {
        assertEquals("call 9876543210", Spoken.fix("call nine eight seven six five four three two one oh"))
        assertEquals(Command.Call("number", "9876543210"), cmd("call nine eight seven six five four three two one zero"))
        assertEquals("call 5550100", Spoken.fix("call five five five oh one oh oh"))
    }

    @Test fun missingPartsAreAskedOnceAndTheAnswerIsAdded() {
        val noTime = cmd("remind me to call mom") as Command.Unclear
        assertEquals("remind me to call mom", noTime.retry)
        assertEquals(Command.Remind("call mom", now.plusMinutes(20)), Commands.parse(Spoken.fix(noTime.retry + " " + "in twenty minutes"), now, true))
        val noWhat = cmd("remind me in ten minutes") as Command.Unclear
        assertEquals(Command.Remind("drink water", now.plusMinutes(10)), Commands.parse(Spoken.fix(noWhat.retry + " " + "drink water"), now, true))
        assertEquals("What should I remind you about?", (cmd("remind me") as Command.Unclear).why)
    }

    @Test fun misheardCallWordsOnlyCountWhenAContactDecides() {
        assertEquals(Command.Call("name", "mom"), cmd("call mom"))
        assertEquals(Command.Call("name", "dad", sure = false), cmd("cool dad"))
        assertEquals(Command.Call("name", "priya", sure = false), cmd("called priya"))
        assertEquals(Command.Call("name", "a share", sure = true, loose = true), cmd("call a share"))
        assertNull(cmd("cool numb is a number 12")) // not a name shape
        assertNull(cmd("call me a taxi"))
        assertNull(cmd("cool"))
    }

    @Test fun alarmIsNoLongerAReminder() {
        assertNull(cmd("set an alarm for 6 am").takeIf { it is Command.Remind })
        assertEquals(AppCommand.Alarm(6, 30, false), AppCommands.parse(Spoken.fix("set an alarm for six thirty a m")))
        assertEquals(AppCommand.Alarm(18, 0, false), AppCommands.parse("set an alarm for 6 pm"))
        assertEquals(AppCommand.Alarm(6, 0, true), AppCommands.parse("set an alarm for 6"))
        assertEquals(AppCommand.Alarm(7, 15, false), AppCommands.parse("wake me up at 7:15 am"))
        assertEquals(AppCommand.Alarm(-1, 0, false), AppCommands.parse("set an alarm"))
    }

    @Test fun timers() {
        assertEquals(AppCommand.Timer(300), AppCommands.parse(Spoken.fix("set a timer for five minutes")))
        assertEquals(AppCommand.Timer(600), AppCommands.parse("start a 10 minute timer"))
        assertEquals(AppCommand.Timer(5400), AppCommands.parse("set a timer for 1 hour 30 minutes"))
        assertEquals(AppCommand.Timer(1800), AppCommands.parse("timer for half an hour"))
        assertEquals(AppCommand.Timer(0), AppCommands.parse("set a timer"))
        assertNull(AppCommands.parse("the timer is broken"))
    }

    @Test fun nextClockOccurrence() {
        assertEquals(18 to 0, Clocks.next(6, 0, true, now)) // 15:00 now: the next 6 o'clock is this evening
        assertEquals(6 to 0, Clocks.next(6, 0, true, now.withHour(20)))
        assertEquals(6 to 0, Clocks.next(6, 0, false, now))
    }

    @Test fun phoneCommands() {
        assertEquals(AppCommand.Flashlight(true), AppCommands.parse("turn on the flashlight"))
        assertEquals(AppCommand.Flashlight(false), AppCommands.parse("turn off the torch"))
        assertEquals(AppCommand.Battery, AppCommands.parse("what is the battery level"))
        assertEquals(AppCommand.Camera, AppCommands.parse("take a selfie"))
        assertEquals(AppCommand.TextMessage("asha", "i am late"), AppCommands.parse("text asha saying i am late"))
        assertEquals(AppCommand.TextMessage("mom", "i will be home soon"), AppCommands.parse("send a message to mom saying i will be home soon"))
    }

    @Test fun yesAndNo() {
        for (y in listOf("yes", "Yeah.", "yep", "yes please", "okay", "sure")) assertEquals(y, true, Answer.yesNo(y))
        for (n in listOf("no", "Nope", "no thanks", "cancel", "wrong")) assertEquals(n, false, Answer.yesNo(n))
        for (o in listOf("play some music", "what time is it", "", "call dad please now yes")) assertNull(o, Answer.yesNo(o))
    }

    @Test fun followUpsLapse() {
        Followups.ask(Pending.Retry("remind me"), now = 1000)
        assertNull(Followups.take(now = 1000 + Followups.LIFETIME_MILLIS + 1))
        Followups.ask(Pending.Retry("remind me"), now = 1000)
        assertTrue(Followups.take(now = 5000) is Pending.Retry)
        assertNull(Followups.take(now = 5000)) // answered once, gone
    }

    private val book = listOf(Contact("Amma", "+91 98765 00001"), Contact("Appa", "+91 98765 00002"), Contact("Asha Rao", "+91 98765 00003"), Contact("Rahul", "+91 98765 00004"), Contact("Priya Nair", "+91 98765 00005"))

    @Test fun familyWordsMatchWhateverTheContactIsCalled() {
        assertEquals(listOf("Amma"), ContactChoice.find("mom", book).contacts.map { it.name })
        assertTrue(ContactChoice.find("mom", book).exact)
        assertEquals(listOf("Appa"), ContactChoice.find("dad", book).contacts.map { it.name })
        assertEquals(listOf("Amma"), ContactChoice.find("mummy", book).contacts.map { it.name })
    }

    @Test fun mishearingsSuggestTheRightPersonFirst() {
        // what the recogniser wrote for Asha, Rahul and Priya in the scratch eval
        assertEquals("Asha Rao", ContactChoice.suggest("a share", book).first().name)
        assertEquals("Asha Rao", ContactChoice.suggest("shy", book).first().name)
        assertEquals("Rahul", ContactChoice.suggest("rattle", book).first().name)
        assertEquals("Priya Nair", ContactChoice.suggest("prayer", book).first().name)
        assertEquals("Priya Nair", ContactChoice.suggest("prior", book).first().name)
        assertTrue(ContactChoice.suggest("quantum physics", book).isEmpty())
    }

    @Test fun learnedSpellingKeyIsStable() {
        assertEquals("a share", ContactAliases.key("  A  Share. "))
    }

    @Test fun songAndArtist() {
        assertEquals(MusicQuery("kesariya by arijit singh", "kesariya", "arijit singh"), MusicQuery.parse("kesariya by arijit singh"))
        assertEquals(MusicQuery("shape of you", null, null), MusicQuery.parse("shape of you"))
    }

    @Test fun readingTimesAloud() {
        assertEquals("5 minutes", SpokenTime.duration(300))
        assertEquals("1 hour 30 minutes", SpokenTime.duration(5400))
        assertEquals("30 seconds", SpokenTime.duration(30))
        assertEquals("6:30 AM", SpokenTime.clock(6, 30))
        assertEquals("12:05 PM", SpokenTime.clock(12, 5))
        assertEquals("12:00 AM", SpokenTime.clock(0, 0))
        assertFalse(SpokenTime.clock(18, 0).contains("18"))
    }
}
