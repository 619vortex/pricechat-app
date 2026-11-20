package live.pricechat.kiwivpn

import java.io.File
import live.pricechat.kiwivpn.ui.LICENCES_ASSET
import org.junit.Assert.assertTrue
import org.junit.Test

/** The licence notices shipped in the APK (Settings > Open-source licences) cover every licence the app's binary carries. */
class LicencesTest {
    private val text = File("src/main/assets/$LICENCES_ASSET").readText()

    @Test
    fun everyLicenceTheBinaryCarriesIsThereInFull() {
        for (part in listOf("WireGuard LLC", "The Go Authors", "Android Open Source Project", "JetBrains", "Square, Inc.")) assertTrue(part, part in text)
        assertTrue("Apache 2.0 in full", text.contains("Apache License") && text.contains("END OF TERMS AND CONDITIONS"))
        assertTrue("MIT in full", text.contains("Permission is hereby granted, free of charge") && text.contains("NONINFRINGEMENT"))
        assertTrue("BSD in full", text.contains("Redistribution and use in source and binary forms") && text.contains("SUCH DAMAGE"))
    }
}
