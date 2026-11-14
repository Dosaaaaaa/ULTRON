import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// The release key lives OUTSIDE the repository (~/.config/ultron/android-signing/); without it the
// release build is produced unsigned. Back that folder up: losing the key means a new app identity.
val signingProps = Properties().apply {
    val f = File(System.getProperty("user.home"), ".config/ultron/android-signing/signing.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.ultron.assistant"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.ultron.assistant"
        minSdk = 26
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") } // Vosk ships 4 CPU variants; phones are arm64, the emulator x86_64
        targetSdk = 35
        versionCode = 11
        versionName = "1.8.0"
    }

    signingConfigs {
        if (signingProps.isNotEmpty()) {
            create("release") {
                storeFile = file(signingProps.getProperty("storeFile"))
                storePassword = signingProps.getProperty("storePassword")
                keyAlias = signingProps.getProperty("keyAlias")
                keyPassword = signingProps.getProperty("keyPassword")
            }
        }
    }

    // -Pultron.testRelease=true runs the on-device tests against the shrunk release build, the one that ships.
    testBuildType = if (providers.gradleProperty("ultron.testRelease").isPresent) "release" else "debug"

    buildTypes {
        debug {
            // The emulator reaches the host's backend over plain http at 10.0.2.2; release builds
            // accept https/wss only (see UrlRules).
            buildConfigField("boolean", "ALLOW_PLAIN_HTTP", "true")
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            if (signingProps.isNotEmpty()) signingConfig = signingConfigs.getByName("release")
            buildConfigField("boolean", "ALLOW_PLAIN_HTTP", "false")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            testProguardFiles("proguard-test-rules.pro")
        }
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.05.01")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.0")
    implementation("androidx.compose.material:material-icons-core")
    // Offline speech engine for hands-free: listens with no beep, no network and no restart gaps.
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    implementation("net.java.dev.jna:jna:5.13.0@aar")
    implementation("com.alphacephei:vosk-android:0.3.75")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303") // the android.jar org.json is only a stub on the JVM
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("com.squareup.okhttp3:okhttp-tls:4.12.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}
