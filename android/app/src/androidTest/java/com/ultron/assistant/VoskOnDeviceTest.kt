package com.ultron.assistant

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.ultron.assistant.core.WakeWord
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.StorageService

/** Runs the real bundled offline model on a real Android runtime, on recorded SYNTHETIC speech (text-to-speech
 *  clips shipped as test assets). No microphone is involved. Proves: the model unpacks from the APK, the
 *  native library loads, the wake name is heard and the command after it is transcribed. */
@RunWith(AndroidJUnit4::class)
class VoskOnDeviceTest {
    private val instr = InstrumentationRegistry.getInstrumentation()

    private fun model(): Model {
        val latch = CountDownLatch(1)
        var m: Model? = null
        var err: IOException? = null
        StorageService.unpack(instr.targetContext, "model-en-us", "vosk-model", { m = it; latch.countDown() }, { err = it; latch.countDown() })
        assertTrue("model unpack timed out", latch.await(240, TimeUnit.SECONDS))
        assertNull("unpack failed: $err", err)
        return m!!
    }

    private fun transcribe(model: Model, asset: String): String {
        val bytes = instr.context.assets.open("$asset.wav").use { it.readBytes() }.drop(44).toByteArray() // 16 kHz mono PCM after the header
        val padded = ByteArray(16000) + bytes + ByteArray(32000)
        val r = Recognizer(model, 16000f)
        val parts = ArrayList<String>()
        var i = 0
        while (i < padded.size) {
            val n = minOf(8000, padded.size - i)
            if (r.acceptWaveForm(padded.copyOfRange(i, i + n), n)) parts += JSONObject(r.result).optString("text")
            i += n
        }
        parts += JSONObject(r.finalResult).optString("text")
        return parts.filter { it.isNotBlank() }.joinToString(" ")
    }

    @Test fun hearsTheWakeNameAndTheCommand() {
        val m = model()
        assertNotNull(m)
        val seen = ArrayList<String>()
        for (a in listOf("jarvis_name_only", "jarvis_hey", "jarvis_play_jazz", "jarvis_play_jazz_f")) {
            val text = transcribe(m, a)
            seen += "$a -> $text"
            assertNotNull("not woken by: $text", WakeWord.match(text, "jarvis"))
        }
        assertEquals("play some jazz", WakeWord.match(transcribe(m, "jarvis_play_jazz"), "jarvis"))
        for (a in listOf("neg_time", "neg_computer")) assertNull(a, WakeWord.match(transcribe(m, a), "jarvis"))
        println("VOSK_ON_DEVICE " + seen.joinToString(" | "))
    }
}
