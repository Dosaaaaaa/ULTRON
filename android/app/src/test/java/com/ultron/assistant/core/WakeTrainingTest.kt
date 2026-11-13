package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeTrainingTest {
    @Test fun countsHitsAndLearnsConsistentVariants() {
        val r = WakeTraining.learn("friday", listOf("friday", "fried a", "fried a", "hey friday", "freda", "friday"))
        assertEquals(3 to 6, r.hits to r.total)
        assertTrue("fried a" in r.learned)
        assertTrue("freda" in r.learned) // close to the name, so a single sighting is enough
    }

    @Test fun learnedVariantsThenWake() {
        val r = WakeTraining.learn("friday", listOf("fried a", "fried a"))
        assertEquals("play jazz", WakeWord.match("fried a play jazz", "friday", r.all))
        assertEquals("", WakeWord.match("hey freda", "friday", listOf("freda")))
        assertNull(WakeWord.match("fried a play jazz", "friday")) // not without training
    }

    @Test fun neverLearnsEverydayWordsOrNoise() {
        val r = WakeTraining.learn("friday", listOf("play", "play", "hello", "hello", "the", "i", "stop", "open the"))
        assertTrue(r.learned.isEmpty())
        assertEquals(0, r.hits)
    }

    @Test fun refusesUnrelatedOneOffs() {
        val r = WakeTraining.learn("friday", listOf("banana", "tomorrow morning"))
        assertTrue(r.learned.isEmpty()) // single sighting and nothing like the name
    }

    @Test fun repeatedUnrelatedWordsAreStillRefused() {
        assertTrue(WakeTraining.learn("friday", listOf("banana", "banana")).learned.isEmpty())
        assertTrue(WakeTraining.learn("friday", listOf("hello", "hello")).learned.isEmpty())
        assertTrue("fryday" in WakeTraining.learn("friday", listOf("fryday")).learned)
    }

    @Test fun capsTheListAndKeepsExisting() {
        val many = (1..20).map { "frida$it" }
        val r = WakeTraining.learn("friday", many + many, existing = listOf("freda"))
        assertTrue(r.all.size <= 12)
        assertNotNull(WakeWord.match("freda", "friday", listOf("freda")))
    }
}
