package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class QuietTrackerTest {
    private fun feed(t: QuietTracker, rms: Float, times: Int) = repeat(times) { t.feed(rms) }

    @Test fun quietAfterSpeechIsCounted() {
        val t = QuietTracker()
        feed(t, 3000f, 8)
        assertEquals(0, t.quietChunks)
        feed(t, 200f, 3)
        assertEquals(3, t.quietChunks)
        assertTrue(t.settled(3))
        assertFalse(t.settled(4))
    }

    @Test fun speechResetsTheCount() {
        val t = QuietTracker()
        feed(t, 3000f, 5); feed(t, 200f, 2); feed(t, 3000f, 1)
        assertEquals(0, t.quietChunks)
    }

    @Test fun roomNoiseAloneIsNeverTreatedAsSpeechFollowedByQuiet() {
        val t = QuietTracker()
        feed(t, 120f, 50) // below the floor: nothing was said
        assertFalse(t.settled(1))
    }

    @Test fun loudNoiseCloseToTheSpeechLevelNeverCountsAsQuiet() {
        val t = QuietTracker()
        feed(t, 3000f, 5)
        feed(t, 1500f, 10) // 0.5 of the speech: a noisy room, not silence
        assertFalse(t.settled(1))
    }

    @Test fun theLevelFollowsTheSpeakerDownSoAWhisperIsNotCountedAsQuiet() {
        val t = QuietTracker()
        feed(t, 6000f, 3)
        feed(t, 600f, 40) // long quiet: the level has decayed
        feed(t, 500f, 1)  // a soft voice now is speech, not silence
        assertEquals(0, t.quietChunks)
    }

    @Test fun resetForgetsEverything() {
        val t = QuietTracker()
        feed(t, 3000f, 5); feed(t, 200f, 3)
        t.reset()
        assertEquals(0, t.quietChunks)
        assertFalse(t.settled(1))
    }
}
