package com.ultron.assistant.core

import java.time.ZoneOffset
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CommandsTest {
    // Thursday 12 Nov 2026, 15:00 at +05:30
    private val now = ZonedDateTime.of(2026, 11, 12, 15, 0, 0, 0, ZoneOffset.ofHoursMinutes(5, 30))
    private fun remind(text: String, guess: Boolean = true) = Commands.parse(text, now, guess) as? Command.Remind
    private fun at(day: Int, h: Int, m: Int = 0) = now.withDayOfMonth(day).withHour(h).withMinute(m).withSecond(0).withNano(0)

    @Test fun relativeTimesAreUnderstood() {
        assertEquals(Command.Remind("call the dentist", now.plusMinutes(30)), remind("remind me to call the dentist in 30 minutes"))
        assertEquals(Command.Remind("take the pizza out", now.plusHours(1)), remind("Remind me in an hour to take the pizza out"))
        assertEquals(Command.Remind("stretch", now.plusMinutes(30)), remind("remind me in half an hour to stretch"))
        assertEquals(Command.Remind("drink water", now.plusHours(2)), remind("remind me to drink water in two hours"))
        assertEquals(Command.Remind("pay 3 bills", now.plusDays(2)), remind("remind me to pay 3 bills in 2 days"))
    }

    @Test fun clearClockTimesAreUnderstood() {
        assertEquals(Command.Remind("take the bins out", at(12, 17, 30)), remind("please remind me at 5:30 pm to take the bins out"))
        assertEquals(Command.Remind("the invoice", at(13, 9)), remind("remind me tomorrow at 9am about the invoice"))
        assertEquals(Command.Remind("submit", at(13, 8)), remind("remind me on friday at 8 am to submit"))
        assertEquals(Command.Remind("pay rent", at(13, 9)), remind("set a reminder for 9am tomorrow: pay rent"))
        assertEquals(Command.Remind("leave", at(12, 17, 30)), remind("remind me at 17:30 to leave"))
        assertEquals(Command.Remind("eat", at(13, 12)), remind("remind me at noon to eat")) // noon has passed today
        assertEquals(Command.Remind("meet Bob at the cafe", at(12, 17)), remind("remind me to meet Bob at 5 pm at the cafe"))
    }

    @Test fun vagueTimesGetASensibleDefaultWithoutAsking() {
        assertEquals(Command.Remind("call mum", at(13, 9)), remind("remind me tomorrow to call mum"))
        assertEquals(Command.Remind("lock up", at(12, 20)), remind("remind me tonight to lock up"))
        assertEquals(Command.Remind("leave", at(12, 17)), remind("remind me at 5 to leave")) // the next 5 o'clock
        assertEquals(Command.Remind("call", at(13, 16)), remind("remind me tomorrow at 4 to call")) // 1-6 with a day: afternoon
        assertEquals(Command.Remind("run", at(13, 8)), remind("remind me tomorrow at 8 to run"))
    }

    @Test fun whenAskingIsOnTheSameVagueTimesAreQuestions() {
        for (t in listOf("remind me tomorrow to call mum", "remind me at 5 to leave", "remind me tonight to lock up")) {
            assertTrue(t, Commands.parse(t, now, guess = false) is Command.Unclear)
        }
    }

    @Test fun aMissingOrImpossibleTimeIsNeverInvented() {
        for (t in listOf("remind me to stretch", "remind me today to call", "remind me at 25:00 to x", "remind me in 5 seconds to x", "remind me in half an hour", "remind me today at 1pm to call")) {
            assertTrue(t, Commands.parse(t, now, guess = true) is Command.Unclear)
        }
    }

    @Test fun callsAreRecognisedByNameAndNumber() {
        assertEquals(Command.Call("name", "Asha"), Commands.parse("call Asha", now))
        assertEquals(Command.Call("name", "Asha Rao"), Commands.parse("please call Asha Rao", now))
        assertEquals(Command.Call("name", "Dr. Patel"), Commands.parse("Ultron, phone Dr. Patel", now))
        assertEquals(Command.Call("name", "mum"), Commands.parse("could you ring mum?", now))
        assertEquals(Command.Call("number", "5550100"), Commands.parse("dial 555 0100", now))
        assertEquals(Command.Call("number", "+442079460958"), Commands.parse("call +44 20 7946 0958", now))
    }

    @Test fun ordinarySentencesAreNotCommands() {
        for (t in listOf("call me a taxi", "let's call it a day", "call me later", "call back", "I will call Asha tomorrow", "call Asha and tell her I am late", "call", "call 12",
            "What is a phone call?", "Do you remind me of someone?", "I set a reminder yesterday", "remind", "")) {
            assertNull(t, Commands.parse(t, now))
        }
    }
}
