package io.github.miffk.bypasshub

import android.app.Application
import io.github.miffk.bypasshub.bhcore.Bhcore
import java.io.File

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Store.init(this)
        val work = File(filesDir, "box").apply { mkdirs() }
        val temp = File(cacheDir, "box").apply { mkdirs() }
        Bhcore.setup(work.path, temp.path)
    }
}
