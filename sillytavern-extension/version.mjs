/**
 * Extension <-> backend version compatibility.
 *
 * Why this exists: the extension ships as static JS inside SillyTavern's
 * `public/` (via a symlink to `sillytavern-extension/`). A SillyTavern
 * reinstall or git update can silently recreate `public/` and drop that
 * symlink, leaving a STALE copy of the extension running against an UPDATED
 * backend with no signal to the user. This module lets the extension detect
 * that on load and warn.
 *
 * The extension cannot know its own git commit at runtime, so the actual
 * compatibility signal is `MEMORY_PROTOCOL_VERSION`: a hand-maintained integer
 * that both sides hard-code. Bump it here AND in `app/version.py`
 * (PROTOCOL_VERSION) whenever the /memory/store or /memory/retrieve contract
 * changes in a way that breaks an older peer. `EXTENSION_VERSION` /
 * backend git_commit are surfaced only as human diagnostics.
 */

// Keep in sync with manifest.json "version".
export const EXTENSION_VERSION = '1.0.0';

// Keep in sync with PROTOCOL_VERSION in app/version.py.
export const MEMORY_PROTOCOL_VERSION = 1;

// Which build of the extension the browser actually loaded. Stamped into every audit
// record: ES modules are cached aggressively (and Android has no hard-reload gesture), so
// "the fix is not working" and "the fix is not loaded" are otherwise indistinguishable -
// which cost several rounds of the live tracker test. Bump on every extension change.
export const MEMORY_EXTENSION_BUILD = '96c4086';

/**
 * Compare the extension's embedded protocol version against the backend's
 * reported version info. Pure and side-effect free so it can be unit tested.
 *
 * @param {object} params
 * @param {number} params.extensionProtocol - MEMORY_PROTOCOL_VERSION
 * @param {object|null} params.backendInfo   - parsed /memory/version body, or null
 * @param {boolean} [params.reachable=true]  - false if the request failed at the network level
 * @returns {{status: string, warn: boolean, message: string, extensionProtocol: number, backendProtocol: (number|null), backendGitCommit: (string|null), backendServiceVersion: (string|null)}}
 *
 * status values:
 *   'ok'               - protocols match; no action
 *   'mismatch'         - protocols differ; warn (direction included in message)
 *   'backend_outdated' - backend did not report a protocol version (predates
 *                        this endpoint / field); warn
 *   'unreachable'      - could not reach the backend; console-only, no banner
 */
export function compareVersions({
    extensionProtocol,
    backendInfo,
    reachable = true,
} = {}) {
    const base = {
        extensionProtocol,
        backendProtocol: null,
        backendGitCommit: null,
        backendServiceVersion: null,
    };

    if (!reachable) {
        return {
            ...base,
            status: 'unreachable',
            warn: false,
            message: 'Could not reach the memory backend to check version compatibility.',
        };
    }

    const backendProtocol = backendInfo && typeof backendInfo.protocol_version === 'number'
        ? backendInfo.protocol_version
        : null;
    const backendGitCommit = backendInfo && backendInfo.git_commit ? String(backendInfo.git_commit) : null;
    const backendServiceVersion = backendInfo && backendInfo.service_version
        ? String(backendInfo.service_version)
        : null;

    const enriched = { ...base, backendProtocol, backendGitCommit, backendServiceVersion };

    if (backendProtocol === null) {
        return {
            ...enriched,
            status: 'backend_outdated',
            warn: true,
            message:
                'The memory backend did not report a protocol version. It is likely older than '
                + 'this extension. Update the backend, or the symlink to the extension may be broken.',
        };
    }

    if (backendProtocol === extensionProtocol) {
        return { ...enriched, status: 'ok', warn: false, message: '' };
    }

    const direction = backendProtocol > extensionProtocol
        ? 'This extension is older than the backend (likely a stale copy - the symlink into '
          + "SillyTavern's public/ may have been overwritten by a reinstall or update)."
        : 'The backend is older than this extension. Update and restart the memory backend.';

    return {
        ...enriched,
        status: 'mismatch',
        warn: true,
        message:
            `Memory protocol mismatch: extension speaks v${extensionProtocol}, backend speaks `
            + `v${backendProtocol}. ${direction}`,
    };
}
