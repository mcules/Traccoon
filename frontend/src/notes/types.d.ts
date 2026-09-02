// PixiJS ships the unsafe-eval plugin without a `types` export condition, so TS
// cannot resolve it. The graph needs that plugin because the content security
// policy forbids `new Function`, which is what the default renderer builds its
// shaders with.
declare module 'pixi.js/unsafe-eval';
