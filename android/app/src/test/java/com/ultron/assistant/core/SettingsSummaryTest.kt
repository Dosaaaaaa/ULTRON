package com.ultron.assistant.core

import org.junit.Assert.assertEquals
import org.junit.Test

class SettingsSummaryTest {
    @Test fun permissionsSayHowManyNeedAnOk() {
        assertEquals("All 5 allowed", SettingsSummary.permissions(missing = 0, total = 5))
        assertEquals("1 needs your OK", SettingsSummary.permissions(missing = 1, total = 5))
        assertEquals("3 need your OK", SettingsSummary.permissions(missing = 3, total = 5))
        assertEquals("None needed", SettingsSummary.permissions(missing = 0, total = 0))
    }

    @Test fun callsLineFollowsTheSwitch() {
        assertEquals("Calls: asks you first", SettingsSummary.calls(confirmCalls = true))
        assertEquals("Calls: start after a 3-second countdown", SettingsSummary.calls(confirmCalls = false))
    }

    @Test fun voiceLineNamesTheWordAndTheChime() {
        assertEquals("Name: Zephyr, chime on", SettingsSummary.voice("Zephyr", chime = true))
        assertEquals("Name: Zephyr, chime off", SettingsSummary.voice("Zephyr", chime = false))
    }

    @Test fun computerLineShowsTheLinkState() {
        assertEquals("Not connected", SettingsSummary.computer(paired = false, connected = false))
        assertEquals("Connected", SettingsSummary.computer(paired = true, connected = true))
        assertEquals("Not reachable now", SettingsSummary.computer(paired = true, connected = false))
    }

    @Test fun notificationsLineSaysWhetherAccessIsAllowedAndHowManyAreKept() {
        assertEquals("Not allowed yet", SettingsSummary.notifications(access = false, kept = 0))
        assertEquals("Allowed, nothing kept", SettingsSummary.notifications(access = true, kept = 0))
        assertEquals("Allowed, 1 kept", SettingsSummary.notifications(access = true, kept = 1))
        assertEquals("Allowed, 12 kept", SettingsSummary.notifications(access = true, kept = 12))
    }
}
