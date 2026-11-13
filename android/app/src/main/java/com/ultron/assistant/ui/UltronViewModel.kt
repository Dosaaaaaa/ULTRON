package com.ultron.assistant.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.ultron.assistant.BuildConfig
import com.ultron.assistant.core.ChatAction
import com.ultron.assistant.core.Fingerprint
import com.ultron.assistant.core.PairLink
import com.ultron.assistant.core.ReminderItem
import com.ultron.assistant.core.UrlRules
import com.ultron.assistant.core.Command
import com.ultron.assistant.core.Display
import com.ultron.assistant.core.Push
import com.ultron.assistant.data.LocalReminders
import com.ultron.assistant.data.Pairing
import com.ultron.assistant.data.Store
import com.ultron.assistant.net.ApiError
import com.ultron.assistant.net.Http
import com.ultron.assistant.net.UltronApi
import com.ultron.assistant.service.PushService
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class ChatLine(val mine: Boolean, val text: String, val actions: List<ChatAction> = emptyList())

/** All the app's state in one place, so rotating the phone or switching tabs loses nothing. */
class UltronViewModel(app: Application) : AndroidViewModel(app) {
    val store = Store(app)
    var pairing by mutableStateOf(store.load())
        private set

    // pairing
    var pairBusy by mutableStateOf(false)
    var pairError by mutableStateOf<String?>(null)
    var pendingLink by mutableStateOf<PairLink?>(null) // from a scanned/opened link, awaiting the operator's OK

    // the phone's own reminders (work with no computer)
    private val localReminders = LocalReminders(app)
    val phoneReminders = mutableStateListOf<ReminderItem>()
    var showPair by mutableStateOf(false) // pairing is optional: it only adds AI chat from the computer

    // settings that decide how much ULTRON asks (calls and reminders are done without asking by default)
    var confirmCalls by mutableStateOf(store.confirmCalls)
        private set
    var guessTimes by mutableStateOf(store.guessTimes)
        private set
    var speakReplies by mutableStateOf(store.speakReplies)
        private set
    var partial by mutableStateOf("") // words heard so far while listening
    var micFixPackage by mutableStateOf<String?>(null) // the app whose microphone permission blocks speech

    // chat
    val lines = mutableStateListOf<ChatLine>()
    var chatBusy by mutableStateOf(false)
    var chatNote by mutableStateOf<String?>(null)
    var listening by mutableStateOf(false)
    private var failedText: String? = null
    private var failedSpoken = false
    val canRetry: Boolean get() = failedText != null
    private var conversation: String? = store.conversationId

    // "ask before acting" switches (null until loaded)
    var prefs by mutableStateOf<com.ultron.assistant.net.Prefs?>(null)
    var prefsNote by mutableStateOf<String?>(null)

    // reminders
    val reminders = mutableStateListOf<ReminderItem>()
    var remindersNote by mutableStateOf<String?>(null)
    var remindersBusy by mutableStateOf(false)

    init { refreshPhoneReminders() }

    private var apiFor: Pair<Pairing, UltronApi>? = null
    private fun api(): UltronApi? {
        val p = pairing ?: return null
        apiFor?.let { if (it.first == p) return it.second }
        return UltronApi(Http.client(p.fingerprint), p.baseUrl, p.token).also { apiFor = p to it }
    }

    private fun explain(e: Throwable, address: String? = pairing?.baseUrl): String = when (e) {
        is ApiError -> when (e.status) {
            401 -> "ULTRON no longer recognises this phone. Remove it from the desktop app and pair again."
            403 -> e.message ?: "This phone is not allowed to do that."
            429 -> "Too many requests; wait a moment."
            503 -> "No model could answer right now."
            else -> e.message ?: "The server answered ${e.status}."
        }
        else -> "Cannot reach ULTRON at ${address ?: "the computer"} (${e.message ?: e.javaClass.simpleName}). The phone must be on the same Wi-Fi as the computer, and the computer's firewall must allow port 8443."
    }

    // ---------------------------------------------------------------- pairing
    fun offerLink(text: String) {
        when (val r = PairLink.parse(text, BuildConfig.ALLOW_PLAIN_HTTP)) {
            is PairLink.Companion.Parsed.Ok -> { pendingLink = r.link; pairError = null }
            is PairLink.Companion.Parsed.Rejected -> pairError = r.why
        }
    }

    fun pairManually(address: String, code: String, fingerprint: String, name: String) {
        val base = when (val r = UrlRules.parse(address, BuildConfig.ALLOW_PLAIN_HTTP)) {
            is UrlRules.Result.Ok -> r.base
            is UrlRules.Result.Rejected -> { pairError = r.why; return }
        }
        val pin = if (base.startsWith("https://")) Fingerprint.normalise(fingerprint) ?: run {
            pairError = "Enter the 64-digit SHA-256 fingerprint shown on your computer."; return
        } else null
        pair(base, code, pin, name)
    }

    fun confirmLink(name: String) {
        val l = pendingLink ?: return
        pendingLink = null
        pair(l.baseUrl, l.code, l.fingerprint, name)
    }

    fun cancelLink() { pendingLink = null }

    private fun pair(base: String, code: String, pin: String?, name: String) {
        pairBusy = true; pairError = null
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { UltronApi(Http.client(pin), base).pair(code, name, pin) } }
            pairBusy = false
            r.onSuccess {
                store.save(it); pairing = it; showPair = false; lines.clear(); conversation = null; store.conversationId = null
                PushService.start(getApplication())
            }.onFailure { pairError = "Pairing failed: ${explain(it, base)}" }
        }
    }

    fun unpair() {
        PushService.stop(getApplication())
        store.clear(); pairing = null; apiFor = null; lines.clear(); reminders.clear(); conversation = null
    }

    // ---------------------------------------------------------------- chat
    /** What the operator typed or said. Reminders and calls are handled right here on the phone; only
     *  other questions go to the computer (and say so plainly when it is not connected). */
    fun send(text: String, spoken: Boolean, speak: (String) -> Unit) {
        if (text.isBlank() || chatBusy) return
        val clean = text.trim()
        lines += ChatLine(true, clean)
        val now = java.time.ZonedDateTime.now()
        when (val cmd = com.ultron.assistant.core.Commands.parse(clean, now, guessTimes)) {
            is Command.Remind -> {
                localReminders.add(cmd.text, cmd.due.toInstant().toEpochMilli())
                refreshPhoneReminders()
                val said = "Reminder set: ${cmd.text}, ${Display.when_(cmd.due.toInstant().toString(), now.toInstant(), now.zone)}."
                lines += ChatLine(false, said, listOf(ChatAction("reminder", "created", said)))
                if (spoken && speakReplies) speak(said)
            }
            is Command.Call -> placeCall(cmd, spoken, speak)
            is Command.Unclear -> {
                lines += ChatLine(false, cmd.why)
                if (spoken && speakReplies) speak(cmd.why)
            }
            null -> if (pairing != null) deliver(clean, spoken, speak) else {
                val msg = "I can set reminders and make calls on this phone. For other questions I need your computer: open Settings and choose Connect."
                lines += ChatLine(false, msg)
                if (spoken && speakReplies) speak(msg)
            }
        }
    }

    private fun placeCall(cmd: Command.Call, spoken: Boolean, speak: (String) -> Unit) {
        val proposal = Push.CallProposal(
            "local-" + System.nanoTime(), "local", cmd.kind, cmd.target, System.currentTimeMillis(), confirm = confirmCalls,
        )
        com.ultron.assistant.service.AutoDialer.handle(getApplication(), proposal)
        val said = if (confirmCalls) "Check your notifications to confirm the call to ${cmd.target}."
        else "Calling ${cmd.target} in a moment. You can cancel from the notification."
        lines += ChatLine(false, said, listOf(ChatAction("call", if (confirmCalls) "awaiting_confirmation" else "placing", said)))
        if (spoken && speakReplies) speak(said)
    }

    fun refreshPhoneReminders() {
        phoneReminders.clear()
        phoneReminders.addAll(
            localReminders.all().map { ReminderItem(it.id, it.text, java.time.Instant.ofEpochMilli(it.dueMillis).toString(), "pending") },
        )
    }

    fun addPhoneReminder(text: String, due: Instant) {
        localReminders.add(text, due.toEpochMilli()); refreshPhoneReminders()
    }

    fun removePhoneReminder(id: String, removed: (ReminderItem) -> Unit) {
        val item = phoneReminders.firstOrNull { it.id == id } ?: return
        localReminders.remove(id); refreshPhoneReminders(); removed(item)
    }

    fun changeConfirmCalls(v: Boolean) { store.confirmCalls = v; confirmCalls = v }
    fun changeGuessTimes(v: Boolean) { store.guessTimes = v; guessTimes = v }
    fun changeSpeakReplies(v: Boolean) { store.speakReplies = v; speakReplies = v }

    /** Try the message that failed again, without showing it twice. */
    fun retry(speak: (String) -> Unit) {
        val t = failedText ?: return
        deliver(t, failedSpoken, speak)
    }

    private fun deliver(text: String, spoken: Boolean, speak: (String) -> Unit) {
        val api = api() ?: return
        chatBusy = true; chatNote = null; failedText = null
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.chat(text, conversation, spoken) } }
            chatBusy = false
            r.onSuccess {
                conversation = it.conversationId ?: conversation; store.conversationId = conversation
                lines += ChatLine(false, it.text, it.actions)
                if (spoken && speakReplies) speak(it.text)
                if (it.actions.any { a -> a.type == "reminder" && a.status == "created" }) loadReminders()
            }.onFailure { chatNote = explain(it); failedText = text; failedSpoken = spoken }
        }
    }

    fun newChat() { lines.clear(); conversation = null; store.conversationId = null; chatNote = null; failedText = null }

    // ---------------------------------------------------------------- ask-before-acting switches
    fun loadPrefs() {
        val api = api() ?: return
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.preferences() } }
            r.onSuccess { prefs = it; prefsNote = null }.onFailure { prefsNote = explain(it) }
        }
    }

    fun setCallConfirm(ask: Boolean) = savePrefs { it.setPreferences(callConfirm = ask) }

    /** [ask] true = ask when a reminder time is unclear; false = use a sensible default instead. */
    fun setAskUnclear(ask: Boolean) = savePrefs { it.setPreferences(reminderGuess = !ask) }

    private fun savePrefs(call: (UltronApi) -> com.ultron.assistant.net.Prefs) {
        val api = api() ?: return
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { call(api) } }
            r.onSuccess { prefs = it; prefsNote = null }.onFailure { prefsNote = explain(it); loadPrefs() }
        }
    }

    // ---------------------------------------------------------------- reminders
    fun loadReminders() {
        val api = api() ?: return
        remindersBusy = true
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.reminders() } }
            remindersBusy = false
            r.onSuccess { reminders.clear(); reminders.addAll(it); remindersNote = null }
                .onFailure { remindersNote = explain(it) }
        }
    }

    fun addReminder(text: String, due: Instant) {
        val api = api() ?: return
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.addReminder(text.trim(), due.toString()) } }
            r.onFailure { remindersNote = explain(it) }.onSuccess { loadReminders() }
        }
    }

    /** Removes a reminder and reports it back, so the screen can offer Undo. */
    fun cancelReminder(id: String, removed: (ReminderItem) -> Unit) {
        val api = api() ?: return
        val item = reminders.firstOrNull { it.id == id } ?: return
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { api.cancelReminder(id) } }
            r.onFailure { remindersNote = explain(it) }.onSuccess { reminders.removeAll { x -> x.id == id }; removed(item) }
        }
    }
}
