// Stand-in call apps, for the emulator only. See README.md. Nothing here goes into Kiwi VPN, and these APKs must never be published.
plugins {
    id("com.android.application")
}

android {
    namespace = "live.pricechat.fakecall"
    compileSdk = 37

    defaultConfig {
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "test"
    }

    flavorDimensions += "who"
    productFlavors {
        create("whatsapp") {
            dimension = "who"
            applicationId = "com.whatsapp"
        }
        create("telegram") {
            dimension = "who"
            applicationId = "org.telegram.messenger"
        }
        create("other") {
            dimension = "who"
            applicationId = "live.pricechat.fakecall.other"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
