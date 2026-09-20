/**
 * Loader. Deliberately trivial and deliberately frozen.
 *
 * SillyTavern loads this file by a fixed URL with no cache-busting
 * (extensions.js: `script.src = /scripts/extensions/${name}/${manifest.js}`), so the browser
 * is free to serve it from cache forever - and on a phone there is no hard-reload gesture to
 * force otherwise. Every import written here would be pinned to whatever version this cached
 * copy happened to name, which is exactly the trap that made the stage-D live test debug a
 * build that no longer existed on disk.
 *
 * So this file holds no logic and never changes: a cached copy of it is as good as a fresh
 * one. It pulls the real entry point in under a URL that is unique per page load, and
 * main.mjs's own imports carry the build stamp (see scripts/stamp_extension_build.py), so a
 * stale module is impossible from here down.
 */
import(`./main.mjs?t=${Date.now()}`);
