package com.ultron.assistant.net

import com.ultron.assistant.core.Fingerprint
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/** The home server uses a self-signed certificate, so the system trust store cannot vouch for it.
 *  Instead the phone trusts exactly one certificate: the one whose SHA-256 fingerprint the operator
 *  saw on the desktop when pairing. Anything else, including a valid public certificate, is refused. */
class PinnedTrust(private val fingerprint: String) : X509TrustManager {
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
        throw CertificateException("client certificates are not used")

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("no certificate presented")
        if (!Fingerprint.matches(fingerprint, leaf.encoded)) {
            throw CertificateException("the server certificate is not the one that was pinned")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

object Http {
    /** `fingerprint == null` means plain http to the emulator host (debug only): system defaults. */
    fun client(fingerprint: String?, pingSeconds: Long = 25): OkHttpClient {
        val b = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS) // the push socket is long-lived; pings detect a dead link
            .pingInterval(pingSeconds, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
        if (fingerprint != null) {
            val trust = PinnedTrust(fingerprint)
            val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
            b.sslSocketFactory(ctx.socketFactory, trust)
            // The certificate itself is pinned, so the name on it (a LAN address) need not match.
            b.hostnameVerifier(HostnameVerifier { _, _ -> true })
        }
        return b.build()
    }
}
