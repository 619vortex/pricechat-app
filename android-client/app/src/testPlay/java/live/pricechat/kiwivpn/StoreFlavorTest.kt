package live.pricechat.kiwivpn

import org.junit.Assert.assertFalse
import org.junit.Test

class StoreFlavorTest {
    @Test fun the_play_build_has_no_auto_call() {
        assertFalse(BuildConfig.AUTO_CALL)
    }
}
