package live.pricechat.kiwivpn

import org.junit.Assert.assertTrue
import org.junit.Test

class StoreFlavorTest {
    @Test fun the_website_build_keeps_auto_call() {
        assertTrue(BuildConfig.AUTO_CALL)
    }
}
