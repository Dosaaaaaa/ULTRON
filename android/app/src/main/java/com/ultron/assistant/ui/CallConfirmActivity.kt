package com.ultron.assistant.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.ultron.assistant.core.CallPolicy
import com.ultron.assistant.core.Contact
import com.ultron.assistant.core.ContactChoice

/** The confirmation that stands between "call Asha" and a call. The number is shown here and the
 *  phone dials only when the operator taps Call. A proposal older than five minutes is refused. */
class CallConfirmActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        edgeToEdge()
        val kind = intent.getStringExtra(EXTRA_KIND).orEmpty()
        val target = intent.getStringExtra(EXTRA_TARGET).orEmpty()
        val created = intent.getLongExtra(EXTRA_CREATED, -1L).takeIf { it >= 0 }
        setContent { UltronTheme { Screen(kind, target, created) } }
    }

    @Composable
    private fun Screen(kind: String, target: String, created: Long?) {
        val fresh = remember { CallPolicy.isFresh(created, System.currentTimeMillis()) }
        var contactsGranted by remember { mutableStateOf(has(Manifest.permission.READ_CONTACTS)) }
        val askContacts = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { contactsGranted = it }
        val askCall = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            pending?.let { number -> if (granted) place(number, direct = true) else place(number, direct = false) }
        }

        val candidates: List<Contact> = remember(contactsGranted) {
            when {
                kind == "number" -> CallPolicy.dialable(target)?.let { listOf(Contact(target, it)) }.orEmpty()
                contactsGranted -> ContactChoice.rank(target, readContacts())
                else -> emptyList()
            }
        }

        Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("Call $target?", style = androidx.compose.material3.MaterialTheme.typography.headlineSmall)
            when {
                !fresh -> {
                    Text("This request is too old to act on (more than five minutes). Ask ULTRON again if you still want the call.")
                    OutlinedButton(onClick = { finish() }) { Text("Close") }
                }
                kind == "name" && !contactsGranted -> {
                    Text("To find $target, ULTRON needs to look in your contacts on this phone. Your contacts are never sent to the server.")
                    Button(onClick = { askContacts.launch(Manifest.permission.READ_CONTACTS) }) { Text("Allow contacts") }
                    OutlinedButton(onClick = { finish() }) { Text("Cancel") }
                }
                candidates.isEmpty() -> {
                    Text("No matching contact or number was found on this phone.")
                    OutlinedButton(onClick = { finish() }) { Text("Close") }
                }
                else -> {
                    Text(if (candidates.size == 1) "Tap Call to dial this number:" else "Several contacts match. Choose one:")
                    candidates.forEach { c ->
                        val number = CallPolicy.dialable(c.number)
                        if (number != null) {
                            Button(
                                modifier = Modifier.fillMaxWidth(),
                                onClick = {
                                    pending = number
                                    if (has(Manifest.permission.CALL_PHONE)) place(number, direct = true)
                                    else askCall.launch(Manifest.permission.CALL_PHONE)
                                },
                            ) { Text("Call ${c.name}  ·  $number") }
                        }
                    }
                    OutlinedButton(onClick = { finish() }) { Text("Cancel") }
                }
            }
            Row { Text("Nothing is dialled until you tap a Call button.", style = androidx.compose.material3.MaterialTheme.typography.bodySmall) }
        }
    }

    private var pending: String? = null

    /** With the permission the call starts at once; without it, the dialer opens with the number
     *  filled in and the operator presses its call button. */
    private fun place(number: String, direct: Boolean) {
        val uri = Uri.fromParts("tel", number, null)
        startActivity(Intent(if (direct) Intent.ACTION_CALL else Intent.ACTION_DIAL, uri))
        finish()
    }

    private fun has(permission: String) = ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun readContacts(): List<Contact> = com.ultron.assistant.data.ContactsReader.read(this)

    companion object {
        const val EXTRA_ID = "id"
        const val EXTRA_KIND = "kind"
        const val EXTRA_TARGET = "target"
        const val EXTRA_CREATED = "created"
    }
}
