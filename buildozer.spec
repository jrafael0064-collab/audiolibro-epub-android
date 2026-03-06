[app]
title = AudioLibro
package.name = audiolibro
package.domain = org.test
source.dir = .
source.include_exts = py,png,jpg,kv,txt,json

version = 0.1

requirements = python3,kivy,plyer,pyjnius

orientation = portrait
android.api = 33
android.minapi = 23
android.ndk = 25b
android.build_tools_version = 34.0.0

# evita byte-compile problemático en algunos builds
p4a.extra_args = --no-byte-compile-python

[buildozer]
log_level = 2
warn_on_root = 1
