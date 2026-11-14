package com.ultron.assistant.core

object TtsEngines {
    /** The engine to ask Android for: the saved one while it is still installed, else null (the phone's default). */
    fun choose(saved: String, installed: List<String>): String? = saved.takeIf { it.isNotBlank() && it in installed }

    fun isGoogle(pkg: String) = pkg.startsWith("com.google.")
}
