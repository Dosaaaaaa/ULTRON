package com.ultron.assistant.net

import com.ultron.assistant.core.Fingerprint
import com.ultron.assistant.core.Push
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Real TLS and real WebSocket traffic against a local test server. */
class NetTest {
    private val servers = mutableListOf<MockWebServer>()

    @After fun stop() = servers.forEach { runCatching { it.shutdown() } }

    private fun tlsServer(): Pair<MockWebServer, HeldCertificate> {
        val cert = HeldCertificate.Builder().commonName("ULTRON").addSubjectAlternativeName("localhost").build()
        val hc = HandshakeCertificates.Builder().heldCertificate(cert).build()
        val s = MockWebServer().apply { useHttps(hc.sslSocketFactory(), false) }
        servers += s
        return s to cert
    }

    private fun fp(cert: HeldCertificate) = Fingerprint.pretty(Fingerprint.of(cert.certificate.encoded))

    @Test fun thePinnedCertificateIsTrusted() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().setBody("""{"device_id":"d1","token":"tok","capabilities":["chat"],"name":"x"}"""))
        server.start()
        val pairing = UltronApi(Http.client(fp(cert)), server.url("/").toString().trimEnd('/')).pair("ABCD-EFGH", "Pixel", fp(cert))
        assertEquals("tok", pairing.token)
        val req = server.takeRequest()
        assertEquals("/v1/devices/pair", req.path)
        assertTrue(req.getHeader("Authorization") == null) // pairing sends no credential: the code is the credential
    }

    @Test fun anyOtherCertificateIsRefusedEvenWhenItIsValidlyFormed() {
        val (server, _) = tlsServer()
        server.enqueue(MockResponse().setBody("{}"))
        server.start()
        val impostor = HeldCertificate.Builder().commonName("ULTRON").build() // same name, different key
        try {
            UltronApi(Http.client(fp(impostor)), server.url("/").toString().trimEnd('/')).pair("ABCD-EFGH", "Pixel", null)
            fail("a certificate that was not pinned must be refused")
        } catch (e: SSLException) {
            assertTrue(e.message.orEmpty() + e.cause?.message, true)
        }
        assertEquals(0, server.requestCount) // nothing, not even the request, reached the server
    }

    @Test fun theDeviceTokenIsSentAsABearerOnChatAndErrorsCarryTheServersMessage() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().setBody("""{"reply":"Hello.","conversation_id":"c1"}"""))
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"error":{"code":"forbidden","message":"Not allowed."}}"""))
        server.start()
        val api = UltronApi(Http.client(fp(cert)), server.url("/").toString().trimEnd('/'), "device-token")
        val reply = api.chat("hi", null, true)
        assertEquals("Hello.", reply.text)
        assertEquals("c1", reply.conversationId)
        val first = server.takeRequest()
        assertEquals("Bearer device-token", first.getHeader("Authorization"))
        assertTrue(first.body.readUtf8().contains("\"modality\":\"voice\""))
        try { api.chat("again", "c1", false); fail() } catch (e: ApiError) {
            assertEquals(403, e.status); assertEquals("Not allowed.", e.message)
        }
    }

    // ------------------------------------------------------------------ the push socket
    private class Recorder {
        val pushes = CopyOnWriteArrayList<Push>()
        val status = CopyOnWriteArrayList<String>()
        val connected = CountDownLatch(1)
    }

    private fun client(server: MockWebServer, cert: HeldCertificate, rec: Recorder, delays: MutableList<Long>) = PushClient(
        Http.client(fp(cert), pingSeconds = 5), server.url("/").toString().trimEnd('/'), "device-token",
        scheduler = { d, task -> delays += d; Thread { Thread.sleep(20); task() }.start() },
        onPush = { rec.pushes += it },
        onStatus = { ok, why -> rec.status += "$ok:$why"; if (ok) rec.connected.countDown() },
    )

    private val notify = """{"type":"push","id":"n1","kind":"notify","payload":{"title":"Hi","body":"there"}}"""

    @Test fun pushesAreShownOnceAcknowledgedEveryTimeAndSentWithTheToken() {
        val (server, cert) = tlsServer()
        val acks = CopyOnWriteArrayList<String>()
        val got = CountDownLatch(2)
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(notify); webSocket.send(notify) // re-sent: at-least-once delivery
            }
            override fun onMessage(webSocket: WebSocket, text: String) { acks += text; got.countDown() }
        }))
        server.start()
        val rec = Recorder()
        val c = client(server, cert, rec, mutableListOf()).also { it.start() }
        assertTrue(got.await(5, TimeUnit.SECONDS))
        c.stop()
        assertEquals(1, rec.pushes.size) // shown once...
        // ...acked twice (compared as JSON: key order is not part of the protocol)
        assertEquals(listOf("ack:n1", "ack:n1"), acks.map { org.json.JSONObject(it).let { o -> "${o.getString("type")}:${o.getString("id")}" } })
        val req = server.takeRequest()
        assertEquals("/v1/ws/device", req.path)
        assertEquals("Bearer device-token", req.getHeader("Authorization"))
    }

    @Test fun aDroppedConnectionIsRetriedWithBackoffAndPushesResume() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { webSocket.close(1001, "restart") }
        }))
        val resumed = CountDownLatch(1)
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { webSocket.send(notify) }
            override fun onMessage(webSocket: WebSocket, text: String) { resumed.countDown() }
        }))
        server.start()
        val rec = Recorder()
        val delays = CopyOnWriteArrayList<Long>()
        val c = client(server, cert, rec, delays).also { it.start() }
        assertTrue(resumed.await(8, TimeUnit.SECONDS))
        c.stop()
        assertEquals(1, rec.pushes.size)
        assertTrue(delays.first() in 1_000L..1_250L)
        assertTrue(rec.status.any { it.startsWith("false:closed") })
    }

    @Test fun aRevokedDeviceIsRefusedAndTheClientKeepsBackingOffInsteadOfHammering() {
        val (server, cert) = tlsServer()
        repeat(3) { server.enqueue(MockResponse().setResponseCode(403)) }
        server.start()
        val rec = Recorder()
        val delays = CopyOnWriteArrayList<Long>()
        val c = client(server, cert, rec, delays).also { it.start() }
        Thread.sleep(600)
        c.stop()
        assertFalse(rec.connected.count == 0L)
        assertTrue(rec.status.any { it.contains("refused (403)") })
        assertTrue(delays.size >= 2 && delays[1] > delays[0]) // longer each time
    }

    @Test fun anImpostorServerNeverReceivesTheToken() {
        val (server, _) = tlsServer()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
        server.start()
        val rec = Recorder()
        val impostor = HeldCertificate.Builder().commonName("ULTRON").build()
        val c = client(server, impostor, rec, CopyOnWriteArrayList()).also { it.start() }
        Thread.sleep(400)
        c.stop()
        assertEquals(0, server.requestCount)
        assertTrue(rec.pushes.isEmpty())
        val unused: Request? = null
        assertTrue(unused == null)
    }

    // ------------------------------------------------------------------ reminders
    @Test fun remindersCanBeListedAddedAndCancelledWithTheDeviceToken() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().setBody("""[{"id":"r1","text":"Stand up","due_at":"2026-11-13T09:00:00+00:00","status":"pending"}]"""))
        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"id":"r2","text":"Call Asha","due_at":"2026-11-13T10:00:00+00:00","status":"pending"}"""))
        server.enqueue(MockResponse().setResponseCode(204))
        server.start()
        val api = UltronApi(Http.client(fp(cert)), server.url("/").toString().trimEnd('/'), "device-token")
        assertEquals(listOf("Stand up"), api.reminders().map { it.text })
        assertEquals("r2", api.addReminder("Call Asha", "2026-11-13T10:00:00Z").id)
        api.cancelReminder("r2")
        val list = server.takeRequest(); val add = server.takeRequest(); val del = server.takeRequest()
        assertEquals("GET /v1/reminders", "${list.method} ${list.path}")
        assertTrue(add.body.readUtf8().contains("\"text\":\"Call Asha\""))
        assertEquals("DELETE /v1/reminders/r2", "${del.method} ${del.path}")
        for (r in listOf(list, add, del)) assertEquals("Bearer device-token", r.getHeader("Authorization"))
    }

    @Test fun aPhoneWithoutTheRemindersPowerGetsTheServersExplanation() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"error":{"code":"forbidden","message":"This device was not paired with the 'reminders' capability."}}"""))
        server.start()
        val api = UltronApi(Http.client(fp(cert)), server.url("/").toString().trimEnd('/'), "device-token")
        try { api.reminders(); fail() } catch (e: ApiError) {
            assertEquals(403, e.status); assertTrue(e.message!!.contains("reminders"))
        }
    }

    @Test fun pairingKeepsThePowersTheServerGranted() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().setBody("""{"device_id":"d1","token":"tok","capabilities":["chat","notify","reminders"],"name":"x"}"""))
        server.start()
        val p = UltronApi(Http.client(fp(cert)), server.url("/").toString().trimEnd('/')).pair("ABCD-EFGH", "Pixel", fp(cert))
        assertEquals(listOf("chat", "notify", "reminders"), p.capabilities)
    }

    @Test fun theAskBeforeActingSwitchesAreReadAndChangedWithTheDeviceToken() {
        val (server, cert) = tlsServer()
        server.enqueue(MockResponse().setBody("""{"call_confirm":true,"reminder_guess":false}"""))
        server.enqueue(MockResponse().setBody("""{"call_confirm":false,"reminder_guess":false}"""))
        server.enqueue(MockResponse().setBody("""{"call_confirm":false,"reminder_guess":true}"""))
        server.start()
        val api = UltronApi(Http.client(fp(cert)), server.url("/").toString().trimEnd('/'), "device-token")
        assertEquals(Prefs(callConfirm = true, reminderGuess = false), api.preferences())
        assertEquals(Prefs(false, false), api.setPreferences(callConfirm = false))
        assertEquals(Prefs(false, true), api.setPreferences(reminderGuess = true))
        val get = server.takeRequest(); val one = server.takeRequest(); val two = server.takeRequest()
        assertEquals("GET /v1/preferences", "${get.method} ${get.path}")
        assertEquals("PATCH", one.method)
        assertEquals("{\"call_confirm\":false}", one.body.readUtf8())  // only the changed switch is sent
        assertEquals("{\"reminder_guess\":true}", two.body.readUtf8())
        for (r in listOf(get, one, two)) assertEquals("Bearer device-token", r.getHeader("Authorization"))
    }
}
