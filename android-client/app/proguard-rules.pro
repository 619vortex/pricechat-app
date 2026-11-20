# GoBackend's native methods are found by name from libwg-go.so, so WireGuard's classes must keep their names in a shrunk build.
-keep class com.wireguard.** { *; }
-dontwarn org.bouncycastle.**
-dontwarn org.conscrypt.**
-dontwarn org.openjsse.**
