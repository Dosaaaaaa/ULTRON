package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeNameTest {
    @Test fun mercuryIsTheDefaultAndStillInTheList() {
        assertEquals("Mercury", WakeWord.DEFAULT)
        assertTrue(WakeWord.CHOICES.containsAll(listOf("Mercury", "Sentinel", "Friday", "Jarvis")))
    }

    @Test fun mercuryWakesOnlyAtTheStartOfASentence() {
        assertEquals("play some jazz", WakeWord.match("mercury play some jazz", "Mercury"))
        assertEquals("", WakeWord.match("hey mercury", "Mercury"))
        assertNull(WakeWord.match("freddie mercury was a singer", "Mercury"))
        assertNull(WakeWord.match("the planet mercury is hot", "Mercury"))
    }

    @Test fun overMusicTheNameIsFoundInTheMiddleOfTheRunningText() {
        assertTrue(WakeWord.late("la la la mercury la la", "Mercury"))
    }

    @Test fun theOldDefaultIsSwitchedOnceAndNothingElseIsTouched() {
        assertEquals("Mercury", WakeWord.migrate("Friday", alreadyDone = false))
        assertEquals("Mercury", WakeWord.migrate(null, alreadyDone = false))
        assertNull(WakeWord.migrate("Friday", alreadyDone = true)) // the user switched back after the change
        assertNull(WakeWord.migrate("Jarvis", alreadyDone = false)) // a deliberate other choice stays
    }
}
