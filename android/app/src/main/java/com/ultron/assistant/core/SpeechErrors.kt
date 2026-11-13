package com.ultron.assistant.core

/** Words for Android's SpeechRecognizer error codes, so a failure says what to do about it instead of
 *  "failed (7)". The numbers are the ERROR_* constants of android.speech.SpeechRecognizer. */
object SpeechErrors {
    const val NETWORK_TIMEOUT = 1
    const val NETWORK = 2
    const val AUDIO = 3
    const val SERVER = 4
    const val CLIENT = 5
    const val SPEECH_TIMEOUT = 6
    const val NO_MATCH = 7
    const val BUSY = 8
    const val INSUFFICIENT_PERMISSIONS = 9
    const val TOO_MANY_REQUESTS = 10
    const val SERVER_DISCONNECTED = 11
    const val LANGUAGE_NOT_SUPPORTED = 12
    const val LANGUAGE_UNAVAILABLE = 13

    fun message(code: Int): String = when (code) {
        NO_MATCH, SPEECH_TIMEOUT -> "I did not catch that. Tap the microphone, start speaking right away, and pause when you are done."
        NETWORK, NETWORK_TIMEOUT -> "Speech recognition needs an internet connection, or an offline language pack in Google's settings."
        AUDIO -> "The microphone could not be read. Close other apps that use it and try again."
        INSUFFICIENT_PERMISSIONS -> "A speech service on this phone has no microphone permission. Tap Fix permission and allow Microphone."
        BUSY -> "Speech recognition is busy. Wait a second and try again."
        CLIENT -> "Speech recognition stopped. Try again."
        SERVER, SERVER_DISCONNECTED, TOO_MANY_REQUESTS -> "Google's speech service is not answering. Try again in a moment."
        LANGUAGE_NOT_SUPPORTED, LANGUAGE_UNAVAILABLE -> "Speech recognition is not available for this language. Download it in Google's voice settings."
        else -> "Speech recognition failed (code $code)."
    }

    /** Codes after which trying the on-device recogniser is worth a go. */
    fun worthTryingOnDevice(code: Int) = code in setOf(CLIENT, SERVER, SERVER_DISCONNECTED, NETWORK, NETWORK_TIMEOUT, LANGUAGE_UNAVAILABLE)
}
