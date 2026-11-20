// Versions checked against Google Maven, Maven Central and the Android Gradle plugin's compatibility table on 2026-11-05. AGP 9 compiles Kotlin itself, so there's no kotlin-android plugin.
plugins {
    id("com.android.application") version "9.5.0" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.21" apply false
}
