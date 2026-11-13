package com.ultron.assistant.core

import java.net.URLDecoder

/** What the desktop's QR code (and the `ultron://pair?...` link) carries: where the server is, the
 *  one-time code, and the certificate fingerprint to pin. Everything is checked before it is used;
 *  a QR code is untrusted input, so a malformed or odd one is refused, never half-applied. */
data class PairLink(val baseUrl: String, val code: String, val fingerprint: String?) {
    companion object {
        sealed interface Parsed {
            data class Ok(val link: PairLink) : Parsed
            data class Rejected(val why: String) : Parsed
        }

        private val codeShape = Regex("^[A-Z0-9]{4}-?[A-Z0-9]{4}$")

        fun parse(text: String, allowPlainHttp: Boolean): Parsed {
            val raw = text.trim()
            if (raw.length > 1500 || !raw.startsWith("ultron://pair?")) return Parsed.Rejected("This is not an ULTRON pairing code.")
            val params = HashMap<String, String>()
            for (part in raw.substringAfter('?').split('&')) {
                val k = part.substringBefore('=', "")
                val v = part.substringAfter('=', "")
                if (k.isEmpty() || k in params) return Parsed.Rejected("The pairing code is malformed.")
                params[k] = try { URLDecoder.decode(v, "UTF-8") } catch (e: Exception) { return Parsed.Rejected("The pairing code is malformed.") }
            }
            val base = when (val r = UrlRules.parse(params["u"].orEmpty(), allowPlainHttp)) {
                is UrlRules.Result.Ok -> r.base
                is UrlRules.Result.Rejected -> return Parsed.Rejected(r.why)
            }
            val code = params["c"].orEmpty().uppercase()
            if (!codeShape.matches(code)) return Parsed.Rejected("The pairing code inside is not valid.")
            val fpText = params["f"]
            val fp = if (fpText == null) null else (Fingerprint.normalise(fpText) ?: return Parsed.Rejected("The certificate fingerprint inside is not valid."))
            if (base.startsWith("https://") && fp == null) return Parsed.Rejected("This code has no certificate fingerprint, so the phone cannot check the server.")
            return Parsed.Ok(PairLink(base, code, fp))
        }
    }
}
