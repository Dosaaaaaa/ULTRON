package com.ultron.assistant.service

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.ultron.assistant.core.Answer
import com.ultron.assistant.core.AppCommand
import com.ultron.assistant.core.AppCommands
import com.ultron.assistant.core.CallPlan
import com.ultron.assistant.core.Command
import com.ultron.assistant.core.Commands
import com.ultron.assistant.core.Contact
import com.ultron.assistant.core.ContactAliases
import com.ultron.assistant.core.ContactChoice
import com.ultron.assistant.core.Display
import com.ultron.assistant.core.Followups
import com.ultron.assistant.core.MediaKey
import com.ultron.assistant.core.Pending
import com.ultron.assistant.core.Push
import com.ultron.assistant.core.Spoken
import com.ultron.assistant.data.ContactsReader
import com.ultron.assistant.data.LocalReminders
import com.ultron.assistant.data.Store
import com.ultron.assistant.net.Http
import com.ultron.assistant.net.UltronApi
import java.time.ZonedDateTime
import java.util.concurrent.Executors

/** What a hands-free sentence means and does. Order: reminders, calls, timers, texts (on this phone), then
 *  controlling apps and music (on this phone), then anything else goes to the computer's ULTRON.
 *
 *  When something is missing ("remind me to call mom" with no time) it asks one short question and the
 *  next thing said, without the wake name, is the answer ([Followups]). */
class VoiceRouter(private val context: Context, onLater: (String) -> Unit = {}) {
    /** [listenAgain]: a question was asked, so the microphone should open for the answer.
     *  [quiet]: the music was asked to stop, so it must stay stopped after the answer. */
    data class Said(val text: String, val stop: Boolean = false, val listenAgain: Boolean = false, val quiet: Boolean = false)

    private val io = Executors.newSingleThreadExecutor()
    private val apps = AppControl(context, onLater) { Store(context).musicApp }

    /** Calls [reply] (possibly on a background thread) with what to say. */
    fun handle(raw: String, reply: (Said) -> Unit) {
        val store = Store(context)
        val text = Spoken.fix(raw)
        when (val p = Followups.take()) {
            is Pending.Retry -> {
                local(Spoken.fix(p.sofar + " " + text), store, p.tries + 1)?.let { reply(it); return }
            }
            is Pending.WhichContact -> Answer.yesNo(text)?.let { reply(answerContact(p, it, store)); return }
            is Pending.ConfirmCall -> Answer.yesNo(text)?.let { reply(if (it) dial(p.contact) else Said("Okay, I will not call.")); return }
            null -> Unit
        }
        local(text, store, 0)?.let { reply(it); return }
        val pairing = store.load()
        if (pairing == null) {
            reply(Said("I can set reminders, timers and alarms, make calls and control apps on this phone. For other questions I need your computer."))
            return
        }
        io.execute {
            val said = runCatching {
                UltronApi(Http.client(pairing.fingerprint), pairing.baseUrl, pairing.token).chat(text, store.conversationId, true).text
            }.getOrElse { "I could not reach your computer." }
            reply(Said(said.take(600)))
        }
    }

    /** A command this phone does by itself, or null when the sentence is for the computer's ULTRON. */
    private fun local(text: String, store: Store, tries: Int): Said? {
        val now = ZonedDateTime.now()
        when (val cmd = Commands.parse(text, now, store.guessTimes)) {
            is Command.Remind -> {
                LocalReminders(context).add(cmd.text, cmd.due.toInstant().toEpochMilli())
                return Said("Reminder set: ${cmd.text}, ${Display.when_(cmd.due.toInstant().toString(), now.toInstant(), now.zone)}.")
            }
            is Command.Call -> return call(cmd, store)
            is Command.Unclear -> return if (cmd.retry != null && tries < 3) ask(cmd.why, cmd.retry, tries) else Said(cmd.why)
            null -> Unit
        }
        return when (val app = AppCommands.parse(text) ?: return null) {
            is AppCommand.Timer -> if (app.seconds <= 0) ask("For how long?", "set a timer for", tries) else Said(apps.run(app))
            is AppCommand.Alarm -> if (app.hour < 0) ask("For what time?", "set an alarm for", tries) else Said(apps.run(app))
            is AppCommand.TextMessage -> textMessage(app)
            else -> Said(apps.run(app), stop = app is AppCommand.StopListening, quiet = app == AppCommand.Media(MediaKey.PAUSE))
        }
    }

    private fun ask(question: String, sofar: String, tries: Int): Said {
        Followups.ask(Pending.Retry(sofar, tries))
        return Said(question, listenAgain = true)
    }

    // ------------------------------------------------------------------ people
    private sealed interface Resolved {
        data class Exact(val contact: Contact) : Resolved
        data class Suggest(val contacts: List<Contact>, val learn: Boolean) : Resolved
        data class Denied(val why: String) : Resolved
        data object None : Resolved
    }

    private fun resolve(spoken: String, store: Store): Resolved {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
            return Resolved.Denied("I need the Contacts permission to find $spoken. Open ULTRON settings and allow Contacts.")
        }
        val all = ContactsReader.read(context)
        store.contactAliases()[ContactAliases.key(spoken)]?.let { name ->
            val hit = all.filter { it.name.equals(name, ignoreCase = true) }.distinctBy { it.number.filter(Char::isDigit) }
            if (hit.size == 1) return Resolved.Exact(hit[0])
        }
        val found = ContactChoice.find(spoken, all)
        if (found.contacts.isNotEmpty()) {
            return if (found.exact && found.contacts.size == 1) Resolved.Exact(found.contacts[0]) else Resolved.Suggest(found.contacts, learn = !found.exact)
        }
        val near = ContactChoice.suggest(spoken, all)
        return if (near.isEmpty()) Resolved.None else Resolved.Suggest(near, learn = true)
    }

    private fun askWhich(heard: String, candidates: List<Contact>, index: Int, body: String?, learn: Boolean): Said {
        Followups.ask(Pending.WhichContact(heard, candidates, index, body, learn))
        val c = candidates[index]
        val lead = if (index == 0) (if (learn) "I heard “$heard”. " else "More than one contact fits. ") else ""
        return Said("${lead}Do you mean ${c.name}?", listenAgain = true)
    }

    private fun call(cmd: Command.Call, store: Store): Said? {
        if (cmd.kind == "number") return plan(Push.CallProposal("local-" + System.nanoTime(), "local", "number", cmd.target, System.currentTimeMillis(), confirm = store.confirmCalls), cmd.target)
        return when (val r = resolve(cmd.target, store)) {
            is Resolved.Denied -> if (cmd.sure && !cmd.loose) Said(r.why) else null
            is Resolved.Exact -> if (store.confirmCalls) {
                Followups.ask(Pending.ConfirmCall(r.contact))
                Said("Call ${r.contact.name}? Say yes to confirm.", listenAgain = true)
            } else dial(r.contact)
            is Resolved.Suggest -> if (cmd.sure) askWhich(cmd.target, r.contacts, 0, null, r.learn) else null
            Resolved.None -> if (cmd.sure && !cmd.loose) Said("I could not find a contact called ${cmd.target}.") else null
        }
    }

    private fun dial(c: Contact): Said =
        plan(Push.CallProposal("local-" + System.nanoTime(), "local", "number", c.number, System.currentTimeMillis(), confirm = false), c.name)

    private fun plan(p: Push.CallProposal, label: String): Said = Said(
        when (val plan = AutoDialer.handle(context, p)) {
            is CallPlan.Dial -> "Calling $label in ${AutoDialer.COUNTDOWN_MILLIS / 1000} seconds. Tap cancel on the notification to stop."
            is CallPlan.Ask -> plan.speech
        },
    )

    private fun textMessage(cmd: AppCommand.TextMessage): Said = when (val r = resolve(cmd.to, Store(context))) {
        is Resolved.Denied -> Said(r.why)
        is Resolved.Exact -> Said(apps.compose(r.contact, cmd.body))
        is Resolved.Suggest -> askWhich(cmd.to, r.contacts, 0, cmd.body, r.learn)
        Resolved.None -> Said("I could not find a contact called ${cmd.to}.")
    }

    private fun answerContact(p: Pending.WhichContact, yes: Boolean, store: Store): Said {
        if (!yes) {
            val next = p.index + 1
            return if (next < p.candidates.size) askWhich(p.heard, p.candidates, next, p.text, p.learn) else Said("Okay. I could not find the right contact.")
        }
        val c = p.candidates[p.index]
        if (p.learn) store.learnContactAlias(p.heard, c.name)
        return if (p.text != null) Said(apps.compose(c, p.text)) else dial(c)
    }

    fun shutdown() = io.shutdown()
}
