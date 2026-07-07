import test from 'node:test';
import assert from 'node:assert/strict';

import {
    EXTENSION_VERSION,
    MEMORY_PROTOCOL_VERSION,
    compareVersions,
} from '../sillytavern-extension/version.mjs';
import { buildCompatibilityBannerMarkup } from '../sillytavern-extension/settings-ui.mjs';

test('embedded extension version matches manifest.json', async () => {
    const { readFile } = await import('node:fs/promises');
    const manifestUrl = new URL('../sillytavern-extension/manifest.json', import.meta.url);
    const manifest = JSON.parse(await readFile(manifestUrl, 'utf-8'));
    assert.equal(EXTENSION_VERSION, manifest.version);
});

test('matching protocol versions report ok and do not warn', () => {
    const result = compareVersions({
        extensionProtocol: MEMORY_PROTOCOL_VERSION,
        backendInfo: {
            protocol_version: MEMORY_PROTOCOL_VERSION,
            service_version: '0.1.0',
            git_commit: 'abc123',
        },
    });
    assert.equal(result.status, 'ok');
    assert.equal(result.warn, false);
    assert.equal(result.backendGitCommit, 'abc123');
});

test('backend ahead of extension warns as a stale-extension mismatch', () => {
    const result = compareVersions({
        extensionProtocol: 1,
        backendInfo: { protocol_version: 2, service_version: '0.2.0' },
    });
    assert.equal(result.status, 'mismatch');
    assert.equal(result.warn, true);
    assert.match(result.message, /extension is older/i);
});

test('backend behind extension warns to update the backend', () => {
    const result = compareVersions({
        extensionProtocol: 3,
        backendInfo: { protocol_version: 2 },
    });
    assert.equal(result.status, 'mismatch');
    assert.equal(result.warn, true);
    assert.match(result.message, /backend is older/i);
});

test('missing protocol_version is treated as an outdated backend', () => {
    const result = compareVersions({
        extensionProtocol: 1,
        backendInfo: { service_version: '0.0.1' },
    });
    assert.equal(result.status, 'backend_outdated');
    assert.equal(result.warn, true);
});

test('unreachable backend does not warn (connection issue, not a mismatch)', () => {
    const result = compareVersions({
        extensionProtocol: 1,
        backendInfo: null,
        reachable: false,
    });
    assert.equal(result.status, 'unreachable');
    assert.equal(result.warn, false);
});

test('banner is empty for ok/no-warn status and populated for a mismatch', () => {
    assert.equal(buildCompatibilityBannerMarkup(null), '');
    assert.equal(
        buildCompatibilityBannerMarkup({ status: 'ok', warn: false, message: '' }),
        '',
    );

    const banner = buildCompatibilityBannerMarkup({
        status: 'mismatch',
        warn: true,
        message: 'Memory protocol mismatch: extension speaks v1, backend speaks v2.',
        extensionProtocol: 1,
        backendProtocol: 2,
        backendServiceVersion: '0.2.0',
        backendGitCommit: 'deadbeef',
    });
    assert.match(banner, /version mismatch/i);
    assert.match(banner, /extension protocol v1/);
    assert.match(banner, /backend protocol v2/);
    assert.match(banner, /deadbeef/);
    assert.match(banner, /data-compat-status="mismatch"/);
});

test('banner escapes untrusted-looking values', () => {
    const banner = buildCompatibilityBannerMarkup({
        status: 'mismatch',
        warn: true,
        message: '<script>alert(1)</script>',
        extensionProtocol: 1,
        backendProtocol: 2,
    });
    assert.doesNotMatch(banner, /<script>/);
    assert.match(banner, /&lt;script&gt;/);
});
