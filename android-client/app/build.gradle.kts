plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// The commit this build came from, passed in by the build script and shown under Settings > App version, so an APK can always be traced to its source.
val gitCommit: String = (project.findProperty("kiwiCommit") as String?) ?: "unknown"

// Release signing comes only from environment variables. See docs/SIGNING.md. Nothing secret is ever in this file or the repository.
val releaseStore: String? = System.getenv("KIWI_KEYSTORE")

android {
    namespace = "live.pricechat.kiwivpn"
    compileSdk = 37

    defaultConfig {
        applicationId = "live.pricechat.kiwivpn"
        minSdk = 26
        targetSdk = 36
        versionCode = 3
        versionName = "0.1.0-rc1"
        testInstrumentationRunner = "live.pricechat.kiwivpn.KiwiTestRunner"
        buildConfigField("String", "GIT_COMMIT", "\"$gitCommit\"")
    }

    signingConfigs {
        if (releaseStore != null) {
            create("release") {
                storeFile = file(releaseStore)
                storePassword = System.getenv("KIWI_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KIWI_KEY_ALIAS")
                keyPassword = System.getenv("KIWI_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (releaseStore != null) signingConfig = signingConfigs.getByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    // Official WireGuard tunnel library, Apache-2.0. All WireGuard cryptography lives in here: see docs/ARCHITECTURE.md.
    implementation("com.wireguard.android:tunnel:1.0.20260102")

    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.browser:browser:1.10.0")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.12.0")
    implementation("com.squareup.okhttp3:okhttp:5.5.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")

    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
}
