// Stand-in for SillyTavern's public/scripts/extensions.js - only what main.mjs imports.
export const extension_settings = {};
globalThis.__stExtensionSettings = extension_settings;

export function getContext() {
    return globalThis.__stHarness.context;
}
