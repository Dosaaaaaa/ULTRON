package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechErrorsTest {
    @Test fun everyKnownCodeHasAnActionableSentenceAndUnknownOnesAreNamed() {
        for (c in 1..13) assertTrue("code $c", SpeechErrors.message(c).length > 20)
        assertTrue(SpeechErrors.message(7).contains("Tap the microphone"))
        assertTrue(SpeechErrors.message(9).contains("Fix permission"))
        assertTrue(SpeechErrors.message(2).contains("internet"))
        assertEquals("Speech recognition failed (code 99).", SpeechErrors.message(99))
    }

    @Test fun theOnDeviceFallbackIsOnlyTriedForServiceTroubleNotForSilence() {
        assertTrue(SpeechErrors.worthTryingOnDevice(SpeechErrors.CLIENT))
        assertTrue(SpeechErrors.worthTryingOnDevice(SpeechErrors.NETWORK))
        assertFalse(SpeechErrors.worthTryingOnDevice(SpeechErrors.NO_MATCH))
        assertFalse(SpeechErrors.worthTryingOnDevice(SpeechErrors.INSUFFICIENT_PERMISSIONS))
    }
}

class AutoCallTest {
    private val now = 1_000_000_000L
    private val book = listOf(Contact("Asha Rao", "+91 98765 43210"), Contact("Ashley Kim", "555 0111"), Contact("Dr. Patel", "555 0122"), Contact("Sam", "555 0133"), Contact("Sam Lee", "555 0144"))
    private fun proposal(kind: String, target: String, confirm: Boolean = false, created: Long? = now) =
        Push.CallProposal("id", "req", kind, target, created, confirm)
    private fun plan(p: Push.CallProposal, call: Boolean = true, contacts: Boolean = true) =
        AutoCall.plan(p, now + 1_000, call, if (contacts) ({ book }) else null)

    @Test fun withConfirmationOnItNeverDialsByItself() {
        assertTrue(plan(proposal("number", "5550100", confirm = true)) is CallPlan.Ask)
    }

    @Test fun aFullNumberIsDialledAfterTheCountdown() {
        assertEquals(CallPlan.Dial("5550100", "5550100"), plan(proposal("number", "5550100")))
    }

    @Test fun oneMatchingContactIsDialledByName() {
        assertEquals(CallPlan.Dial("5550122", "Dr. Patel"), plan(proposal("name", "patel")))
    }

    @Test fun severalMatchesOrNoneAreNeverGuessed() {
        assertTrue(plan(proposal("name", "as")) is CallPlan.Ask) // Asha Rao and Ashley Kim share the start
        assertTrue(plan(proposal("name", "zed")) is CallPlan.Ask)
    }

    @Test fun anExactNameBeatsLongerNames() {
        assertEquals(CallPlan.Dial("5550133", "Sam"), plan(proposal("name", "sam")))
    }

    @Test fun missingPermissionsOldRequestsAndBadNumbersFallBackToAsking() {
        assertTrue(plan(proposal("number", "5550100"), call = false) is CallPlan.Ask)
        assertTrue(plan(proposal("name", "patel"), contacts = false) is CallPlan.Ask)
        assertTrue(AutoCall.plan(proposal("number", "5550100", created = now - 10 * 60_000), now, true, null) is CallPlan.Ask)
        assertTrue(AutoCall.plan(proposal("number", "5550100", created = null), now, true, null) is CallPlan.Ask)
        assertTrue(plan(proposal("number", "12")) is CallPlan.Ask)
    }

    @Test fun theProposalDefaultsToAskingWhenTheServerSaysNothing() {
        val raw = """{"type":"push","id":"c","kind":"call_proposal","created_at":"2026-11-12T10:00:00+00:00","payload":{"request_id":"q","kind":"name","target":"Asha"}}"""
        val p = (PushParser.parse(raw) as Incoming.Item).push as Push.CallProposal
        assertTrue(p.confirm)
        val off = raw.replace("\"target\":\"Asha\"", "\"target\":\"Asha\",\"confirm\":false")
        assertFalse(((PushParser.parse(off) as Incoming.Item).push as Push.CallProposal).confirm)
    }

    @Test fun theChatActionForAutomaticCallsSaysSo() {
        val r = ChatParser.reply(org.json.JSONObject("""{"reply":"ok","actions":[{"type":"call","status":"placing","target":"Asha"}]}"""))
        assertEquals("Calling now. You can cancel during the countdown.", r.actions[0].summary)
    }
}

class ServiceChoiceTest {
    private val tts = RecognizerService("com.google.android.tts", "com.google.android.apps.speech.tts.googletts.service.GoogleTTSRecognitionService", hasMicPermission = false)
    private val aiai = RecognizerService("com.google.android.as", "com.google.android.apps.miphone.aiai.app.AiAiSpeechRecognitionService", hasMicPermission = true)
    private val tts2 = tts.copy(hasMicPermission = true)

    @Test fun theBlockedDefaultIsSkippedForAServiceThatMayUseTheMicrophone() {
        // The real phone: default = Speech Services by Google (no mic permission), another service has it.
        val c = ServiceChoice.pick("com.google.android.tts/${tts.className}", listOf(tts, aiai))
        assertEquals(ServiceChoice.Use(aiai), c)
    }

    @Test fun theDefaultIsPreferredWhenItIsAllowed() {
        assertEquals(ServiceChoice.Use(tts2), ServiceChoice.pick("com.google.android.tts/${tts.className}", listOf(aiai, tts2)))
    }

    @Test fun ifNoneMayUseItTheAppToFixIsTheDefaultOne() {
        val c = ServiceChoice.pick("com.google.android.tts/${tts.className}", listOf(aiai.copy(hasMicPermission = false), tts)) as ServiceChoice.Blocked
        assertEquals("com.google.android.tts", c.fix.packageName)
    }

    @Test fun noServicesAtAllIsReportedAsSuch() {
        assertEquals(ServiceChoice.NoneInstalled, ServiceChoice.pick(null, emptyList()))
        assertTrue(ServiceChoice.pick(null, listOf(aiai)) is ServiceChoice.Use)
    }
}
