package com.ultron.assistant.core

import com.ultron.assistant.service.Chime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CallAndChimeTest {
    private val book = listOf(Contact("Asha Sharma", "+91 98765 43210"), Contact("Mum", "555 0100"), Contact("Rohit", "555 0111"), Contact("Rohan", "555 0112"))
    private fun proposal(target: String, kind: String = "name", confirm: Boolean = false) =
        Push.CallProposal("p", "d", kind, target, 1000L, confirm = confirm)
    private fun plan(p: Push.CallProposal, perm: Boolean = true, contacts: Boolean = true) =
        AutoCall.plan(p, 2000L, perm, if (contacts) ({ book }) else null)

    @Test fun exactNameDials() {
        val p = plan(proposal("mum"))
        assertEquals(CallPlan.Dial("5550100", "Mum"), p)
    }

    @Test fun misheardNameOnlyAsksAndSaysWho() {
        val p = plan(proposal("aasha")) as CallPlan.Ask
        assertTrue(p.speech, "Asha Sharma" in p.speech)
        assertTrue(p.notify)
        assertTrue(plan(proposal("a shah")) is CallPlan.Ask)
        assertTrue(plan(proposal("mom")) is CallPlan.Dial) // "mom" and "Mum" are the same person, and only one contact is called that
        assertTrue(plan(proposal("mommah")) is CallPlan.Ask) // a loose guess never auto-dials
    }

    @Test fun unknownNameIsSaidAloudAndNotNotified() {
        val p = plan(proposal("zebediah")) as CallPlan.Ask
        assertEquals("I could not find a contact called zebediah.", p.speech)
        assertFalse(p.notify)
    }

    @Test fun ambiguousAndPermissionCasesSayWhatToDo() {
        assertTrue((plan(proposal("ro")) as CallPlan.Ask).speech.isNotBlank())
        assertTrue("Phone permission" in (plan(proposal("mum"), perm = false) as CallPlan.Ask).speech)
        assertTrue("Contacts permission" in (plan(proposal("mum"), contacts = false) as CallPlan.Ask).speech)
        assertTrue("confirm" in (plan(proposal("mum", confirm = true)) as CallPlan.Ask).speech)
    }

    @Test fun numbersDialAndSoundexMatchesNames() {
        assertEquals(CallPlan.Dial("5550100", "5550100"), plan(proposal("555 0100", "number")))
        assertEquals(ContactChoice.soundex("asha"), ContactChoice.soundex("aasha"))
        assertTrue(ContactChoice.find("rohit", book).exact)
        assertEquals(listOf("Rohit"), ContactChoice.find("rohit", book).contacts.map { it.name })
    }

    @Test fun chimeIsShortQuietAndClickFree() {
        for (k in Chime.Kind.values()) {
            val s = Chime.samples(k)
            assertTrue(Chime.durationMillis(k) in 60..300)
            assertEquals(0, s.first().toInt()); assertEquals(0, s.last().toInt())
            val peak = s.maxOf { kotlin.math.abs(it.toInt()) }
            assertTrue(peak in 3000..16000)
        }
        assertTrue(Chime.durationMillis(Chime.Kind.READY) > Chime.durationMillis(Chime.Kind.GOT_IT))
    }
}
