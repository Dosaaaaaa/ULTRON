package com.ultron.assistant.core

/** Which speech-recognition service to use. Android picks one for every app (here: Speech Services by
 *  Google), but if THAT app has not been given the microphone permission every request fails with
 *  "insufficient permissions". So: prefer the phone's default if it may use the microphone, else any
 *  installed service that may, and only if none may, say exactly which app needs the permission. */
data class RecognizerService(val packageName: String, val className: String, val hasMicPermission: Boolean)

sealed interface ServiceChoice {
    data class Use(val service: RecognizerService) : ServiceChoice
    /** Services exist but none may use the microphone; [fix] is the one to open in settings. */
    data class Blocked(val fix: RecognizerService, val others: List<RecognizerService>) : ServiceChoice
    data object NoneInstalled : ServiceChoice

    companion object {
        /** [defaultComponent] is the phone's `voice_recognition_service` setting ("pkg/cls"), if any. */
        fun pick(defaultComponent: String?, services: List<RecognizerService>): ServiceChoice {
            if (services.isEmpty()) return NoneInstalled
            val default = services.firstOrNull { defaultComponent != null && defaultComponent == "${it.packageName}/${it.className}" }
            val ordered = listOfNotNull(default) + services.filter { it != default }
            ordered.firstOrNull { it.hasMicPermission }?.let { return Use(it) }
            return Blocked(default ?: services.first(), services)
        }
    }
}
