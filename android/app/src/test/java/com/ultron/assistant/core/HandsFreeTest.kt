package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HandsFreeTest {
    @Test fun nameAloneWakes() {
        for (s in listOf("Ultron", "ultron.", "Altron", "Oltron", "Ultrahn", "Ultran", "all tron", "ultra on", "Hey Ultron", "okay ultron")) {
            assertEquals(s, "", WakeWord.match(s))
        }
    }

    @Test fun commandAfterTheNameIsReturned() {
        assertEquals("play some jazz", WakeWord.match("Ultron, play some jazz"))
        assertEquals("open spotify", WakeWord.match("Hey Altron open Spotify"))
    }

    @Test fun otherWordsAndMidSentenceMentionsDoNotWake() {
        for (s in listOf("", "hello there", "ultra", "electron", "neutron", "cutron", "retron", "alderon", "patron", "ultimate", "tell me about ultron", "I watched age of ultron", "so then ultron said hi")) {
            assertNull(s, WakeWord.match(s))
        }
    }

    @Test fun chosenNamesWakeOnlyAtTheStart() {
        assertEquals("play jazz", WakeWord.match("jarvis play jazz", "Jarvis"))
        assertEquals("", WakeWord.match("hey jarvis", "jarvis"))
        assertEquals("open spotify", WakeWord.match("okay computer open spotify", "Computer"))
        assertNull(WakeWord.match("I need a new computer", "computer"))
        assertNull(WakeWord.match("see you on friday", "friday"))
        assertNull(WakeWord.match("jarvis", "nova"))
        assertNull(WakeWord.match("ultron", "jarvis")) // the old name no longer wakes once another is chosen
        assertEquals("", WakeWord.match("altron", "ultron"))
        for (n in WakeWord.CHOICES) assertEquals(n, "", WakeWord.match(n, n))
    }

    @Test fun appCommands() {
        assertEquals(AppCommand.OpenApp("spotify"), AppCommands.parse("open spotify"))
        assertEquals(AppCommand.OpenApp("YouTube Music"), AppCommands.parse("Please launch the YouTube Music app."))
        assertEquals(AppCommand.PlayMusic("bohemian rhapsody", "spotify"), AppCommands.parse("play bohemian rhapsody on spotify"))
        assertEquals(AppCommand.PlayMusic("some jazz", null), AppCommands.parse("play some jazz"))
        assertEquals(AppCommand.Media(MediaKey.PLAY), AppCommands.parse("play"))
        assertEquals(AppCommand.Media(MediaKey.PLAY), AppCommands.parse("resume the music"))
        assertEquals(AppCommand.Media(MediaKey.PAUSE), AppCommands.parse("pause"))
        assertEquals(AppCommand.Media(MediaKey.PAUSE), AppCommands.parse("stop the music"))
        assertEquals(AppCommand.Media(MediaKey.NEXT), AppCommands.parse("next song"))
        assertEquals(AppCommand.Media(MediaKey.PREVIOUS), AppCommands.parse("previous"))
        assertEquals(AppCommand.Volume(true), AppCommands.parse("volume up"))
        assertEquals(AppCommand.Volume(false), AppCommands.parse("quieter"))
        assertEquals(AppCommand.Navigate("the airport"), AppCommands.parse("navigate to the airport"))
        assertEquals(AppCommand.WebSearch("best pizza near me"), AppCommands.parse("search for best pizza near me"))
        assertEquals(AppCommand.TellTime, AppCommands.parse("what time is it"))
        assertEquals(AppCommand.StopListening, AppCommands.parse("stop listening"))
    }

    @Test fun ordinaryQuestionsAreNotAppCommands() {
        for (s in listOf("what is the capital of France", "tell me a joke", "how are you", "remind me in 5 minutes to stretch", "call Asha")) {
            assertNull(s, AppCommands.parse(s))
        }
    }

    @Test fun appMatchingIsStrict() {
        val labels = listOf("Spotify", "YouTube", "YouTube Music", "WhatsApp", "Maps", "Clock", "Camera")
        assertEquals("Spotify", AppMatch.best("spotify", labels))
        assertEquals("YouTube Music", AppMatch.best("youtube music", labels))
        assertEquals("YouTube", AppMatch.best("youtube", labels))
        assertEquals("WhatsApp", AppMatch.best("whats app", labels))
        assertNull(AppMatch.best("you", labels))
        assertNull(AppMatch.best("the door", labels))
        assertNull(AppMatch.best("x", labels))
    }
}
