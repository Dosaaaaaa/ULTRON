package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NoteFilterTest {
    private val sms = "com.google.android.apps.messaging"
    private val wa = "com.whatsapp"
    private val gm = "com.google.android.gm"
    private val teams = "com.microsoft.teams"

    private fun judge(pkg: String, title: String, text: String) = NoteFilter.judge(pkg, title, text)
    private fun kept(v: Verdict) = v is Verdict.Keep
    private fun dropped(v: Verdict) = v is Verdict.Drop

    // ---------------------------------------------------------------- must be dropped: codes, banks, payments
    private val mustDrop = listOf(
        "Your OTP is 482913. Do not share it with anyone.",
        "482913 is your verification code",
        "Use 7391 as your Amazon login code",
        "Your one-time password is 55 12 90",
        "G-123456 is your Google verification code",
        "Your WhatsApp code: 123-456",
        "Security code 884211. It expires in 10 minutes.",
        "Never share your PIN with anyone",
        "Your CVV is 123",
        "HDFC Bank: Rs 5,000 debited from a/c XX1234 on 12-Nov",
        "A/c XX1234 credited INR 4,500.00 by UPI",
        "UPI payment of Rs 200 to Ramesh successful",
        "Txn of Rs 999 at AMAZON on your card ending 4321",
        "Your available balance is Rs 12,300. IMPS ref 3321",
        "Sensitive notification content hidden",
        "NEFT credit of 25000 received",
        "2FA code: 902133",
    )

    @Test fun codesBankAndPaymentTextIsDroppedWhateverTheApp() {
        for (app in listOf(sms, wa, gm, teams)) for (t in mustDrop) {
            assertTrue("$app should drop: $t", dropped(judge(app, "Someone", t)))
        }
    }

    @Test fun theTitleIsCheckedToo() {
        assertTrue(dropped(judge(wa, "Your verification code is 123456", "tap to copy")))
        assertTrue(dropped(judge(gm, "HDFC Bank", "Rs 5,000 debited from a/c XX1234")))
    }

    @Test fun businessSenderIdsAreDroppedFromTextMessages() {
        for (id in listOf("VM-HDFCBK", "AD-SBIINB", "JD-AMAZON", "TX-SWIGGY-S", "HDFCBK", "AX-ICICIT")) {
            assertTrue("sender id $id", dropped(judge(sms, id, "Your order has shipped")))
        }
    }

    // ---------------------------------------------------------------- must pass: people
    @Test fun ordinaryMessagesFromPeopleAreKept() {
        assertEquals(Verdict.Keep(Source.WHATSAPP), judge(wa, "Asha", "are we meeting at 5?"))
        assertEquals(Verdict.Keep(Source.EMAIL), judge(gm, "Rahul", "Lunch tomorrow?"))
        assertEquals(Verdict.Keep(Source.SMS), judge(sms, "+91 98765 43210", "call me when you are free"))
        assertEquals(Verdict.Keep(Source.SMS), judge(sms, "Mom", "dinner at 8, bring 2 bottles"))
        assertEquals(Verdict.Keep(Source.TEAMS), judge(teams, "Priya", "standup moved to 10:30"))
        assertTrue(kept(judge(wa, "Dev", "can you send me Rs 500 for lunch? I will pay you back")))
        assertTrue(kept(judge(wa, "Asha", "happy birthday! party at 7:30, flat 204")))
        assertTrue(kept(judge(gm, "Priya", "Re: balance sheet review on Monday")))
    }

    // ---------------------------------------------------------------- apps
    @Test fun onlyTheFourListedKindsOfAppAreRead() {
        assertEquals(Verdict.Ignore, judge("com.instagram.android", "someone", "liked your photo"))
        assertEquals(Verdict.Ignore, judge("com.spotify.music", "Now playing", "a song"))
        assertEquals(Source.WHATSAPP, NoteFilter.source("com.whatsapp.w4b"))
        assertEquals(Source.SMS, NoteFilter.source("com.android.mms"))
    }

    @Test fun bankPaymentAuthenticatorAndPasswordAppsAreNeverReadEvenIfListed() {
        for (pkg in listOf("com.phonepe.app", "net.one97.paytm", "com.google.android.apps.nbu.paisa.user", "com.sbi.lotusintouch",
            "com.hdfcbank.mobile", "com.google.android.apps.authenticator2", "com.x8bit.bitwarden", "com.onepassword.android")) {
            assertTrue("$pkg", NoteFilter.neverRead(pkg))
            assertTrue(!kept(judge(pkg, "Asha", "hello")))
        }
        assertTrue(!NoteFilter.neverRead("com.whatsapp"))
    }

    @Test fun anExtraPackageCanBeMappedForDebugBuildsOnly() {
        assertEquals(Verdict.Ignore, NoteFilter.judge("com.android.shell", "A", "b"))
        assertEquals(Verdict.Keep(Source.SMS), NoteFilter.judge("com.android.shell", "A", "b", mapOf("com.android.shell" to Source.SMS)))
    }

    @Test fun emptyNotificationsAreIgnored() {
        assertEquals(Verdict.Ignore, judge(wa, "", ""))
        assertEquals(Verdict.Ignore, judge(wa, "  ", " "))
    }
}
