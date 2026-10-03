/**
 * Module-resolution hook: main.mjs reaches SillyTavern through `../../../extensions.js`
 * and `../../../../script.js`, which do not exist outside an ST checkout. Point exactly
 * those two at the stubs next to this file; everything else resolves as usual.
 */
const STUBS = {
    'extensions.js': new URL('./st-extensions.mjs', import.meta.url).href,
    'script.js': new URL('./st-script.mjs', import.meta.url).href,
};

export async function resolve(specifier, context, nextResolve) {
    const fromExtension = context.parentURL?.includes('/sillytavern-extension/');
    const name = specifier.split('/').pop();
    if (fromExtension && specifier.startsWith('../') && STUBS[name]) {
        return { url: STUBS[name], shortCircuit: true };
    }
    return nextResolve(specifier, context);
}
