package com.ultron.assistant.data

import android.content.Context
import android.provider.ContactsContract
import com.ultron.assistant.core.Contact

/** The phone's own contact list, read only when the operator asked to call a name. Never uploaded. */
object ContactsReader {
    fun read(context: Context, limit: Int = 5000): List<Contact> {
        val out = ArrayList<Contact>()
        val cols = arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER)
        context.contentResolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, cols, null, null, null)?.use { c ->
            while (c.moveToNext() && out.size < limit) {
                val name = c.getString(0) ?: continue
                val number = c.getString(1) ?: continue
                out += Contact(name, number)
            }
        }
        return out
    }
}
