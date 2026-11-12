package android.content.res

/** Compile-only Android boundary. Host tests must use explicit file/JSON inputs. */
class AssetManager {
    fun open(name: String): java.io.InputStream =
        throw UnsupportedOperationException("Android AssetManager is unavailable in host tests: $name")
}
