package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class QuickCommandsTest {
    @Test fun shortFixedCommandsAreQuick() {
        assertEquals(AppCommand.Media(MediaKey.PAUSE), QuickCommands.parse("pause"))
        assertEquals(AppCommand.Media(MediaKey.PAUSE), QuickCommands.parse("stop the music"))
        assertEquals(AppCommand.Media(MediaKey.NEXT), QuickCommands.parse("next song"))
        assertEquals(AppCommand.Media(MediaKey.NEXT), QuickCommands.parse("skip"))
        assertEquals(AppCommand.Media(MediaKey.PREVIOUS), QuickCommands.parse("previous"))
        assertEquals(AppCommand.Volume(true), QuickCommands.parse("louder"))
        assertEquals(AppCommand.Volume(false), QuickCommands.parse("volume down"))
    }

    @Test fun anythingThatCouldContinueIsNotQuick() {
        // "play" is the start of "play some jazz": it must wait for the end of the sentence.
        assertNull(QuickCommands.parse("play"))
        assertNull(QuickCommands.parse("play some jazz"))
        assertNull(QuickCommands.parse("next week"))
        assertNull(QuickCommands.parse("stop listening"))
        assertNull(QuickCommands.parse("call asha"))
        assertNull(QuickCommands.parse("open spotify"))
        assertNull(QuickCommands.parse(""))
        assertNull(QuickCommands.parse("what time is it"))
    }

    @Test fun theActionIsTheAnswerForMediaKeysAndVolume() {
        assertTrue(QuickCommands.silent(AppCommand.Media(MediaKey.PAUSE)))
        assertTrue(QuickCommands.silent(AppCommand.Media(MediaKey.NEXT)))
        assertTrue(QuickCommands.silent(AppCommand.Media(MediaKey.PREVIOUS)))
        assertTrue(QuickCommands.silent(AppCommand.Volume(true)))
        assertFalse(QuickCommands.silent(AppCommand.Media(MediaKey.PLAY)))
        assertFalse(QuickCommands.silent(AppCommand.OpenApp("spotify")))
        assertFalse(QuickCommands.silent(AppCommand.TellTime))
    }

    @Test fun theFinalResultOfAnAlreadyFiredCommandIsRecognisedAsTheSame() {
        val fired = QuickCommands.parse("next song")!!
        assertTrue(QuickCommands.sameAs(fired, "next song"))
        assertTrue(QuickCommands.sameAs(fired, "skip"))
        assertFalse(QuickCommands.sameAs(fired, "pause"))
        assertFalse(QuickCommands.sameAs(fired, "play some jazz"))
    }
}
