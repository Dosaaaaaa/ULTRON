package com.ultron.assistant.core

/** Whether a call proposal can be placed without the operator's tap. This is only reached when the
 *  operator switched "confirm before calling" off, and even then the phone dials by itself only for a
 *  full number or exactly one matching contact. Anything unclear still asks, whatever the switch says. */
sealed interface CallPlan {
    data class Dial(val number: String, val label: String) : CallPlan
    /** [speech] is what to say aloud; [notify] is false when there is nothing for a confirmation screen to show. */
    data class Ask(val reason: String, val speech: String = reason, val notify: Boolean = true) : CallPlan
}

object AutoCall {
    fun plan(
        proposal: Push.CallProposal,
        nowMillis: Long,
        hasCallPermission: Boolean,
        /** null when the contacts permission is missing */
        contacts: (() -> List<Contact>)?,
    ): CallPlan {
        val who = proposal.target
        if (proposal.confirm) return CallPlan.Ask("confirmation is on", "Check your notifications to confirm the call to $who.")
        if (!CallPolicy.isFresh(proposal.createdAtMillis, nowMillis)) return CallPlan.Ask("the request is too old", "That request is too old. Ask again.", notify = false)
        if (!hasCallPermission) return CallPlan.Ask("this phone may not place calls yet", "I need the Phone permission to call. Open ULTRON settings and allow Phone calls; for now check your notifications.")
        if (proposal.kind == "number") {
            val n = CallPolicy.dialable(proposal.target) ?: return CallPlan.Ask("that is not a dialable number", "That is not a number I can dial.", notify = false)
            return CallPlan.Dial(n, n)
        }
        val all = contacts?.invoke() ?: return CallPlan.Ask("contacts are not allowed on this phone", "I need the Contacts permission to find $who. Open ULTRON settings and allow Contacts.")
        val found = ContactChoice.find(who, all)
        val matches = found.contacts
        return when {
            matches.isEmpty() -> CallPlan.Ask("no contact matches “$who”", "I could not find a contact called $who.", notify = false)
            !found.exact -> CallPlan.Ask(
                "“$who” only sounds like ${matches.first().name}",
                "I heard $who. Is it ${matches.first().name}? Check your notifications to confirm.",
            )
            matches.size == 1 -> CallPolicy.dialable(matches[0].number)?.let { CallPlan.Dial(it, matches[0].name) }
                ?: CallPlan.Ask("that contact has no usable number", "${matches[0].name} has no number I can dial.", notify = false)
            else -> CallPlan.Ask("${matches.size} contacts match “$who”", "${matches.size} contacts match $who. Check your notifications to choose.")
        }
    }
}
