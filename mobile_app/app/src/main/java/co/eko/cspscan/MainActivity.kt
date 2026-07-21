package co.eko.cspscan

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import java.io.File

/**
 * CSP Scan — a thin WebView shell around the client-side scanner page
 * (csp_dashboard/mobile_scanner/scan.html, bundled into assets at build time).
 *
 * The page is served through WebViewAssetLoader under the virtual https origin
 * https://appassets.androidplatform.net/ — a SECURE CONTEXT, which the Web Crypto
 * API (.cspx encryption) and the camera file-chooser both require. All OCR /
 * Excel / encryption run inside the page on this device; nothing is uploaded.
 */
class MainActivity : ComponentActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraImageUri: Uri? = null
    private lateinit var chooserLauncher: ActivityResultLauncher<Intent>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Result of the "camera or file" chooser -> hand the URI back to the page.
        chooserLauncher = registerForActivityResult(
            ActivityResultContracts.StartActivityForResult()
        ) { result ->
            val cb = filePathCallback
            filePathCallback = null
            if (cb == null) return@registerForActivityResult
            var uris: Array<Uri>? = null
            if (result.resultCode == Activity.RESULT_OK) {
                val picked = result.data?.data
                uris = when {
                    picked != null -> arrayOf(picked)          // a file / gallery pick
                    cameraImageUri != null -> arrayOf(cameraImageUri!!)  // a fresh photo
                    else -> null
                }
            }
            cb.onReceiveValue(uris ?: arrayOf())
        }

        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        webView = WebView(this)
        setContentView(webView)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mediaPlaybackRequiresUserGesture = false
        }

        webView.webViewClient = object : WebViewClientCompat() {
            override fun shouldInterceptRequest(
                view: WebView,
                request: WebResourceRequest
            ): WebResourceResponse? = assetLoader.shouldInterceptRequest(request.url)
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                cameraImageUri = null

                val wantsPdf = params.acceptTypes.any { it.contains("pdf") }
                val content = Intent(Intent.ACTION_GET_CONTENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    if (wantsPdf) {
                        type = "*/*"
                        putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/pdf", "image/*"))
                    } else {
                        type = "image/*"
                    }
                }

                val extras = ArrayList<Intent>()
                createCameraIntent()?.let { extras.add(it) }

                val chooser = Intent(Intent.ACTION_CHOOSER).apply {
                    putExtra(Intent.EXTRA_INTENT, content)
                    putExtra(Intent.EXTRA_TITLE, "Select")
                    if (extras.isNotEmpty()) {
                        putExtra(Intent.EXTRA_INITIAL_INTENTS, extras.toTypedArray())
                    }
                }

                return try {
                    chooserLauncher.launch(chooser)
                    true
                } catch (e: Exception) {
                    filePathCallback = null
                    false
                }
            }

            // Grant getUserMedia if the page ever asks (not used today, harmless).
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread { request.grant(request.resources) }
            }
        }

        webView.loadUrl("https://appassets.androidplatform.net/assets/scan.html")
    }

    /** Camera intent that writes the photo to a FileProvider URI we can read back. */
    private fun createCameraIntent(): Intent? {
        val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        if (intent.resolveActivity(packageManager) == null) return null
        return try {
            val file = File.createTempFile("scan_", ".jpg", cacheDir)
            cameraImageUri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            intent.putExtra(MediaStore.EXTRA_OUTPUT, cameraImageUri)
            intent
        } catch (e: Exception) {
            null
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) webView.goBack()
        else @Suppress("DEPRECATION") super.onBackPressed()
    }
}
