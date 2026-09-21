/**
 * The settings key is the one name in this extension that does not match the others.
 *
 * The directory is `memoryst`, the manifest's display_name is `memoryst`, the panel
 * header says memoryst - and the values are stored under `extension_settings`
 * ['memory-service']. That is deliberate: nothing derives the key from the directory
 * name, so renaming the string would orphan every stored value in place, and ST would
 * restart from defaults with no error to explain it.
 *
 * `main.mjs` already carries a comment saying so, and that comment protects the code:
 * whoever opens `main.mjs` reads it. It does nothing for the person who never opens
 * `main.mjs` - the one editing `data/<user>/settings.json` by hand, who finds a key
 * matching no installed extension directory and deletes it as leftovers.
 *
 * So the same fact is stated where that person actually is: in the settings panel and
 * in the README's troubleshooting list. Prose drifts from code silently, which is the
 * failure this file exists to make loud - if the key ever moves, these assertions fail
 * and name the documents that still claim the old one.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (relative) => readFileSync(join(ROOT, relative), 'utf8');

const MAIN = read('sillytavern-extension/main.mjs');
const PANEL = read('sillytavern-extension/settings-ui.mjs');
const README = read('sillytavern-extension/README.md');
const MANIFEST = JSON.parse(read('sillytavern-extension/manifest.json'));

/** The key as the code actually declares it - read, not repeated, so this cannot drift. */
const SETTINGS_KEY = MAIN.match(/const SETTINGS_KEY = '([^']+)'/)?.[1];

test('the settings key is declared exactly once, as a literal', () => {
    assert.ok(SETTINGS_KEY, 'no `const SETTINGS_KEY = \'...\'` found in main.mjs');
    assert.equal(MAIN.match(/const SETTINGS_KEY =/g).length, 1);
});

test('the key still differs from the display name, so the warning is still needed', () => {
    // If these are ever reconciled, this whole file - and the three documents it
    // guards - can go. Until then the mismatch is the hazard.
    assert.notEqual(SETTINGS_KEY, MANIFEST.display_name);
});

test('the comment explaining why it is not renamed is still there', () => {
    assert.match(MAIN, /must NOT be "tidied up" to match/);
});

test('the settings panel names the key it saves under', () => {
    assert.ok(
        PANEL.includes(SETTINGS_KEY),
        `the panel never mentions '${SETTINGS_KEY}', so someone reading settings.json `
        + 'cannot tell which extension owns that key',
    );
    assert.match(PANEL, /settings\.json/);
});

test('the README troubleshooting list covers the settings-reset symptom', () => {
    const troubleshooting = README.slice(README.indexOf('## Troubleshooting'));
    assert.ok(troubleshooting.length > 0, 'README has no Troubleshooting section');
    assert.ok(
        troubleshooting.includes(SETTINGS_KEY),
        `Troubleshooting never mentions '${SETTINGS_KEY}' - the symptom is "everything `
        + 'reset to defaults", and that is the term someone will search for',
    );
    assert.match(troubleshooting, /settings\.json/);
});

test('the README explains the mismatch rather than only stating it', () => {
    // A bare "the key is memory-service" invites the rename it is there to prevent.
    assert.match(README, /SETTINGS_KEY/);
});
