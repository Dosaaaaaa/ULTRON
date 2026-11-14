package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DictationTest {
    @Test fun theFinalTextWinsWhenThereIsOne() {
        assertEquals("what time is it", Dictation.pick("what time is it", "what time"))
    }

    @Test fun tappingAgainUsesTheWordsHeardSoFar() {
        assertEquals("what time", Dictation.pick(null, "what time"))
        assertEquals("what time", Dictation.pick("  ", "  what time "))
    }

    @Test fun nothingHeardIsNull() {
        assertNull(Dictation.pick(null, ""))
        assertNull(Dictation.pick("", "   "))
    }
}
