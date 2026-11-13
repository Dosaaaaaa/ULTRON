package com.ultron.assistant.core

import java.security.MessageDigest

/** SHA-256 certificate fingerprints, written the way the backend and `openssl x509 -fingerprint`
 *  print them (AA:BB:...). Comparison ignores case, colons and spaces, never the digits. */
object Fingerprint {
    fun normalise(text: String): String? {
        val hex = text.trim().removePrefix("sha256 Fingerprint=").filter { it != ':' && !it.isWhitespace() }.uppercase()
        return if (hex.length == 64 && hex.all { it in '0'..'9' || it in 'A'..'F' }) hex else null
    }

    fun pretty(hex: String): String = hex.chunked(2).joinToString(":")

    fun of(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02X".format(it) }

    fun matches(expected: String, der: ByteArray): Boolean {
        val want = normalise(expected) ?: return false
        return MessageDigest.isEqual(want.toByteArray(), of(der).toByteArray())
    }
}
