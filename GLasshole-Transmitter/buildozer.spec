[app]
# (str) Title of your application
title = GLasshole-Transmitter

# (str) Package name
package.name = tglasstransmitter

# (str) Package domain (needed for android/ios packaging)
package.domain = org.tglass

# (str) Source code where main.py lives
source.dir = .

# (str) Main entry point
source.include_exts = py,png

# (str) Application version
version = 1.0.2

icon.filename = %(source.dir)s/icon.png

# (list) Application requirements
requirements = python3,kivy,pyjnius,numpy

# (str) Supported orientation (portrait, landscape, all or sensor)
orientation = portrait

# (str) Presplash of the application
# presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon of the application
# icon.filename = %(source.dir)s/data/icon.png

# (str) Supported Android API
android.api = 35
android.minapi = 24

android.add_src = android_src

# (str) Android application permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_STATE,ACCESS_COARSE_LOCATION,ACCESS_FINE_LOCATION,FOREGROUND_SERVICE,FOREGROUND_SERVICE_MEDIA_PROJECTION,POST_NOTIFICATIONS

# (str) Python-for-Android bootstrap
android.archs = arm64-v8a

p4a.branch = develop

# MediaProjection foreground service (required for Android 14+)
services = mediaprojection:services/mediaprojection.py:foreground:foregroundServiceType=mediaProjection

# (str) Full debug output
log_level = 2

# (str) Presplash background color
# presplash_color = #FFFFFF

# (str) Android adaptive icon background color
# android.adaptive_icon_foreground.filename = %(source.dir)s/data/icon-foreground.png
# android.adaptive_icon_background.filename = %(source.dir)s/data/icon-background.png

[buildozer]
# (str) Log level (0 = error only, 1 = warning, 2 = info, 3 = debug)
log_level = 2

# (str) Warn if buildozer is run as root
warn_on_root = 1
