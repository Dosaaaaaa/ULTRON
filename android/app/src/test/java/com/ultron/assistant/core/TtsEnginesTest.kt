package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TtsEnginesTest {
    private val installed = listOf("com.google.android.tts", "org.example.opentts")

    @Test fun aSavedEngineIsUsedWhileItIsInstalled() {
        assertEquals("org.example.opentts", TtsEngines.choose("org.example.opentts", installed))
    }

    @Test fun anUninstalledOrEmptyChoiceFallsBackToThePhonesDefault() {
        assertNull(TtsEngines.choose("org.removed.engine", installed))
        assertNull(TtsEngines.choose("", installed))
    }

    @Test fun googlesEngineIsRecognisedSoSettingsCanSayWhoHearsTheText() {
        assertTrue(TtsEngines.isGoogle("com.google.android.tts"))
        assertFalse(TtsEngines.isGoogle("org.example.opentts"))
    }
}
