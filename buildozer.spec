[app]
title = AudioLibro
package.name = audiolibro
package.domain = org.test
source.dir = .
source.include_exts = py,kv,png,jpg,ttf,txt

version = 0.1

requirements = python3,kivy,plyer,pyjnius,ebooklib,beautifulsoup4,lxml

orientation = portrait

android.api = 33
android.minapi = 23
android.ndk = 25b
android.build_tools_version = 34.0.0

[buildozer]
log_level = 2
warn_on_root = 1
