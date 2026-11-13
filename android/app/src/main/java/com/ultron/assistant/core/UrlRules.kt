package com.ultron.assistant.core

import java.net.URI

/** Which backend addresses the app will talk to. The token is a bearer credential: it only ever
 *  travels over TLS, except in debug builds talking to the emulator's host alias. */
object UrlRules {
    private val plainHttpHosts = setOf("10.0.2.2", "localhost", "127.0.0.1")

    sealed interface Result {
        data class Ok(val base: String) : Result
        data class Rejected(val why: String) : Result
    }

    fun parse(input: String, allowPlainHttp: Boolean): Result {
        val text = input.trim().trimEnd('/')
        val uri = try { URI(text) } catch (e: Exception) { return Result.Rejected("That is not a valid address.") }
        val host = uri.host ?: return Result.Rejected("The address needs a host, like https://192.168.1.20:8443")
        if (uri.userInfo != null) return Result.Rejected("Do not put a user name or password in the address.")
        if (!uri.query.isNullOrEmpty() || !uri.fragment.isNullOrEmpty()) {
            return Result.Rejected("The address must not have a query or fragment.")
        }
        if (!uri.path.isNullOrEmpty()) return Result.Rejected("Use just the host and port, without a path.")
        return when (uri.scheme?.lowercase()) {
            "https" -> Result.Ok(text)
            "http" ->
                if (allowPlainHttp && host in plainHttpHosts) Result.Ok(text)
                else Result.Rejected("Use https: the token must not cross the network in clear text.")
            else -> Result.Rejected("The address must start with https://")
        }
    }

    fun webSocketUrl(base: String, path: String): String =
        base.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") + path
}
