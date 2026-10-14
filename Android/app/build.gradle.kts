import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Версия — та же, что у BypassHub для Windows (BypassHub/bypasshub/__init__.py)
val appVersion: String = Regex("""__version__\s*=\s*"([^"]+)"""")
    .find(rootProject.file("../BypassHub/bypasshub/__init__.py").readText())!!.groupValues[1]
val appVersionCode: Int = appVersion.split(".").map { it.toInt() }.let { (a, b, c) -> a * 10000 + b * 100 + c }

// Подпись релиза: секреты репозитория (см. Android/README.md). Без них — отладочная подпись.
val keystoreFile = System.getenv("BH_KEYSTORE_FILE")?.let { file(it) }

android {
    namespace = "io.github.miffk.bypasshub"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.miffk.bypasshub"
        minSdk = 24
        targetSdk = 35
        versionCode = appVersionCode
        versionName = appVersion
    }

    signingConfigs {
        if (keystoreFile != null && keystoreFile.exists()) {
            create("release") {
                storeFile = keystoreFile
                storePassword = System.getenv("BH_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("BH_KEY_ALIAS")
                keyPassword = System.getenv("BH_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    packaging {
        jniLibs.useLegacyPackaging = true
    }
}

dependencies {
    implementation(files("libs/bhcore.aar"))
}
