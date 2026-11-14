package com.ultron.assistant.core

object Dictation {
    /** What to send when listening ends: the finished sentence if the recogniser delivered one, else the words
     *  heard so far (the button was tapped again before the pause), else nothing. */
    fun pick(finalText: String?, lastPartial: String): String? =
        finalText?.trim()?.takeIf { it.isNotEmpty() } ?: lastPartial.trim().takeIf { it.isNotEmpty() }
}
