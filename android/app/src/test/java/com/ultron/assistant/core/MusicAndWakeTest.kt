package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MusicAndWakeTest {
    // ------------------------------------------------------------------ the name over music
    @Test fun overMusicTheNameCountsAnywhereInTheNewWords() {
        assertTrue(WakeWord.late("the and oh you know friday", "Friday"))
        assertTrue(WakeWord.late("friday", "friday"))
        assertTrue(WakeWord.late("love me like you do hey friday what", "friday", from = 5))
        assertFalse(WakeWord.late("love me like you do", "friday"))
        assertFalse(WakeWord.late("", "friday"))
    }

    @Test fun wordsAlreadyLookedAtAreNotCountedAgain() {
        // "friday" was in the text last time (word 2); only words from index 4 on are new.
        assertFalse(WakeWord.late("see you friday night and then", "friday", from = 4))
        assertTrue(WakeWord.late("see you friday night and friday", "friday", from = 4))
        assertFalse(WakeWord.late("friday", "friday", from = 3))
    }

    @Test fun learnedSpellingsCountOverMusicToo() {
        assertTrue(WakeWord.late("the the oh fry day", "friday", listOf("fry day")))
        assertTrue(WakeWord.late("and so right a", "friday", listOf("righta")))
        assertFalse(WakeWord.late("the the oh fry", "friday", listOf("fry day")))
        assertEquals(5, WakeWord.count("The the, oh fry day!"))
    }

    @Test fun inQuietTheNameStillOnlyCountsAtTheStart() {
        assertNull(WakeWord.match("see you on friday", "friday"))
        assertEquals("pause", WakeWord.match("friday pause", "friday"))
    }

    @Test fun aReplyThatSaysTheNameIsRecognisedAsSuch() {
        assertTrue(WakeWord.late("It is 4:10 PM on Friday, 13 November 2026.", "friday"))
        assertFalse(WakeWord.late("Timer set for 5 minutes.", "friday"))
    }

    // ------------------------------------------------------------------ asking for a song
    @Test fun theAppCanComeFirst() {
        assertEquals(AppCommand.PlayMusic("kesariya", "apple music"), AppCommands.parse("open apple music and play kesariya"))
        assertEquals(AppCommand.PlayMusic("tum hi ho", "apple music"), AppCommands.parse("open the apple music app and play the song tum hi ho"))
        assertEquals(AppCommand.PlayMusic("rock and roll", "spotify"), AppCommands.parse("launch spotify and then play rock and roll"))
        assertEquals(AppCommand.PlayMusic("believer", "apple music"), AppCommands.parse("on apple music play believer"))
        assertEquals(AppCommand.PlayMusic("believer", "apple music"), AppCommands.parse("in apple music, play believer"))
    }

    @Test fun waysOfAskingForASong() {
        assertEquals(AppCommand.PlayMusic("kesariya", null), AppCommands.parse("play the song kesariya"))
        assertEquals(AppCommand.PlayMusic("kesariya", null), AppCommands.parse("play me kesariya"))
        assertEquals(AppCommand.PlayMusic("kesariya", null), AppCommands.parse("play a song called kesariya"))
        assertEquals(AppCommand.PlayMusic("shape of you", null), AppCommands.parse("I want to listen to shape of you"))
        assertEquals(AppCommand.PlayMusic("shape of you", "apple music"), AppCommands.parse("can you play shape of you in apple music"))
        assertEquals(AppCommand.PlayMusic("kesariya", "apple music"), AppCommands.parse("play kesariya from apple music"))
        assertEquals(AppCommand.PlayMusic("dance on the floor", "apple music"), AppCommands.parse("play dance on the floor in apple music"))
        assertEquals(AppCommand.PlayMusic("kesariya by arijit singh", "apple music"), AppCommands.parse("play kesariya by arijit singh on apple music"))
    }

    @Test fun playWithNoTitleIsThePlayKey() {
        for (s in listOf("play", "play music", "play some music", "play something", "play my songs", "resume", "continue the song")) {
            assertEquals(s, AppCommand.Media(MediaKey.PLAY), AppCommands.parse(s))
        }
    }

    @Test fun openingAnAppIsStillJustOpeningIt() {
        assertEquals(AppCommand.OpenApp("apple music"), AppCommands.parse("open apple music"))
        assertEquals(AppCommand.OpenApp("play store"), AppCommands.parse("open play store"))
        assertEquals(AppCommand.OpenApp("google play games"), AppCommands.parse("open google play games"))
    }

    @Test fun questionsForTheComputerAreNotSongs() {
        for (s in listOf("I want to hear a joke", "listen to me", "what should I play tonight", "who plays the lead in that film")) {
            assertNull(s, AppCommands.parse(s))
        }
    }

    // ------------------------------------------------------------------ did the right song start
    private fun q(s: String) = MusicQuery.parse(s)

    @Test fun theRightSongFits() {
        assertTrue(MusicMatch.fits(q("kesariya"), "Kesariya (From \"Brahmastra\")", "Pritam, Arijit Singh & Amitabh Bhattacharya", "Brahmastra"))
        assertTrue(MusicMatch.fits(q("kesaria"), "Kesariya", "Arijit Singh", null)) // one letter apart
        assertTrue(MusicMatch.fits(q("shape of you"), "Shape of You", "Ed Sheeran", "÷ (Deluxe)"))
        assertTrue(MusicMatch.fits(q("tum hi ho by arijit singh"), "Tum Hi Ho", "Arijit Singh", "Aashiqui 2"))
        assertTrue(MusicMatch.fits(q("kesariya brahmastra"), "Kesariya", "Arijit Singh", "Brahmastra (Original Motion Picture Soundtrack)"))
    }

    @Test fun anArtistAloneAcceptsAnyOfTheirSongs() {
        assertTrue(MusicMatch.fits(q("arijit singh"), "Tum Hi Ho", "Arijit Singh", "Aashiqui 2"))
        assertTrue(MusicMatch.fits(q("a r rahman"), "Jai Ho", "A.R. Rahman", null))
    }

    @Test fun anotherSongDoesNotFit() {
        assertFalse(MusicMatch.fits(q("kesariya"), "Tum Hi Ho", "Arijit Singh", "Aashiqui 2"))
        assertFalse(MusicMatch.fits(q("kesariya arijit singh"), "Tum Hi Ho", "Arijit Singh", "Aashiqui 2")) // right singer, wrong song
        assertFalse(MusicMatch.fits(q("kesariya by arijit singh"), "Channa Mereya", "Arijit Singh", null))
        assertFalse(MusicMatch.fits(q("believer"), "Thunder", "Imagine Dragons", "Evolve"))
        assertFalse(MusicMatch.fits(q("believer"), null, null, null))
    }

    @Test fun openRequestsAreNotChecked() {
        for (s in listOf("some jazz", "arijit singh songs", "my playlist", "the latest hits", "something relaxing", "workout mix")) assertFalse(s, MusicMatch.checkable(q(s)))
        for (s in listOf("kesariya", "shape of you", "tum hi ho by arijit singh")) assertTrue(s, MusicMatch.checkable(q(s)))
    }
}
