"""NUR QA: oeffnet ein echtes WebView2-Fenster (pywebview, Backend edgechromium - dieselbe Engine wie die
Lexono-Desktop-App) auf die uebergebene, isolierte QA-Instanz. CDP-Port kommt ueber WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS."""
import sys

import webview

webview.create_window("Lexono QA (WebView2)", sys.argv[1], width=1600, height=900)
webview.start(gui="edgechromium", private_mode=True)
