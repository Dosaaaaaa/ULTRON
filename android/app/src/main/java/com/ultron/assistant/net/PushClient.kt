package com.ultron.assistant.net

import com.ultron.assistant.core.Backoff
import com.ultron.assistant.core.Incoming
import com.ultron.assistant.core.Push
import com.ultron.assistant.core.PushParser
import com.ultron.assistant.core.Seen
import com.ultron.assistant.core.UrlRules
import java.util.concurrent.atomic.AtomicBoolean
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject

/** The always-on connection to the device socket. Reconnects with backoff, acknowledges every item
 *  after the app has shown it, and shows an item that is re-sent only once. */
class PushClient(
    private val http: OkHttpClient,
    private val baseUrl: String,
    private val token: String,
    private val scheduler: (delayMillis: Long, task: () -> Unit) -> Unit,
    private val onPush: (Push) -> Unit,
    private val onStatus: (connected: Boolean, detail: String) -> Unit,
) {
    private val backoff = Backoff()
    private val seen = Seen()
    private val running = AtomicBoolean(false)
    @Volatile private var socket: WebSocket? = null

    fun start() {
        if (running.compareAndSet(false, true)) connect()
    }

    fun stop() {
        running.set(false)
        socket?.close(1000, "stopping")
        socket = null
        onStatus(false, "stopped")
    }

    private fun connect() {
        if (!running.get()) return
        val req = Request.Builder().url(UrlRules.webSocketUrl(baseUrl, "/v1/ws/device"))
            .header("Authorization", "Bearer $token").build()
        socket = http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                backoff.reset()
                onStatus(true, "connected")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                when (val m = PushParser.parse(text)) {
                    is Incoming.Item -> {
                        if (seen.firstTime(m.push.id)) onPush(m.push)
                        // Acknowledge also a repeat: the first ack may have been lost with the link.
                        webSocket.send(JSONObject().put("type", "ack").put("id", m.push.id).toString())
                    }
                    is Incoming.Failure -> onStatus(true, "server said: ${m.code}")
                    else -> Unit
                }
            }

            // A polite close from the server arrives as onClosing; the app must answer it, then retry.
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                lost("closed ($code)")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost("closed ($code)")

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                // 403/401 after a revoke: do not hammer the server; the backoff ceiling is one minute.
                lost(response?.let { "refused (${it.code})" } ?: (t.message ?: "no connection"))
            }

            private val handled = AtomicBoolean(false) // closing, closed and failure can all fire

            private fun lost(why: String) {
                if (!running.get() || !handled.compareAndSet(false, true)) return
                onStatus(false, why)
                scheduler(backoff.nextMillis()) { connect() }
            }
        })
    }
}
