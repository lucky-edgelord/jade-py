/* Jade's avatar, taken from the Jade Vault app by scripts/extract_avatar.py (do not edit here; re-run that).
   Needs window.S (settings), $, esc and saveSettings from the page, and pixi + Cubism loaded first. */
const AV = '/ui/avatar/';
const JADE_THEMES = Object.freeze({
  jade:      { name: 'Jade',      accent: '#1fe5a5', rgb: [31, 229, 165],  highlight: '#ffb8c6', highlightRgb: [255, 184, 198], line: '#1d6b52', ink: '#00140c' },
  ruby:      { name: 'Ruby',      accent: '#f34b68', rgb: [243, 75, 104], highlight: '#ffb7c2', highlightRgb: [255, 183, 194], line: '#792a3d', ink: '#25050b' },
  amethyst:  { name: 'Amethyst',  accent: '#b58af4', rgb: [181, 138, 244], highlight: '#e5c9ff', highlightRgb: [229, 201, 255], line: '#5e477d', ink: '#160b25' },
  gold:      { name: 'Gold',      accent: '#f4c95d', rgb: [244, 201, 93], highlight: '#ffe3a3', highlightRgb: [255, 227, 163], line: '#79602c', ink: '#211800' },
  sapphire:  { name: 'Sapphire',  accent: '#62a4ff', rgb: [98, 164, 255], highlight: '#b9d6ff', highlightRgb: [185, 214, 255], line: '#365a89', ink: '#061426' },
  diamond:   { name: 'Diamond',   accent: '#9cecf4', rgb: [156, 236, 244], highlight: '#d9fbff', highlightRgb: [217, 251, 255], line: '#3f747d', ink: '#061619' },
  'rose-gold': { name: 'Rose Gold', accent: '#e7a0a5', rgb: [231, 160, 165], highlight: '#ffd0c7', highlightRgb: [255, 208, 199], line: '#785052', ink: '#261011' },
  emerald:   { name: 'Emerald',   accent: '#24d18b', rgb: [36, 209, 139], highlight: '#a6f1d1', highlightRgb: [166, 241, 209], line: '#176d4b', ink: '#001a10' },
  obsidian:  { name: 'Obsidian',  accent: '#aebbd0', rgb: [174, 187, 208], highlight: '#d6dfec', highlightRgb: [214, 223, 236], line: '#515e72', ink: '#111722' },
  quartz:    { name: 'Quartz',    accent: '#e3b5ed', rgb: [227, 181, 237], highlight: '#f5dcfb', highlightRgb: [245, 220, 251], line: '#705478', ink: '#211224' },
  spectrum:  { name: 'Spectrum', accent: '#bd72f3', rgb: [189, 114, 243], highlight: '#e1c0fa', highlightRgb: [225, 192, 250], line: '#57366f', ink: '#160d1c' },
  'custom-gradient': { name: 'Custom Gradient', accent: '#f34b68', rgb: [243, 75, 104], highlight: '#62a4ff', highlightRgb: [98, 164, 255], line: '#792a3d', ink: '#160a1d' },
});
const JADE_APPEARANCE_PARTS = Object.freeze([
  { id: 'hair', label: 'Hair' }, { id: 'clothes', label: 'Clothes' }, { id: 'stockings', label: 'Stockings' }, { id: 'boots', label: 'Boots' }, { id: 'skin', label: 'Skin' },
]);
const JADE_SKIN_TONES = Object.freeze({
  porcelain: { label: 'Porcelain', color: '#f2d6d1' }, fair: { label: 'Fair', color: '#e9c1ac' }, light: { label: 'Light', color: '#d8a181' },
  medium: { label: 'Medium', color: '#bd805f' }, tan: { label: 'Tan', color: '#9b654a' }, deep: { label: 'Deep', color: '#704533' },
});
// Source-texture rectangles for the bundled 2048×2048 atlas; keep these in sync with l2d-tex.
const JADE_APPEARANCE_REGIONS = Object.freeze({
  skin: [[0, 0, 220, 280], [0, 270, 320, 510], [600, 250, 850, 500], [560, 560, 820, 700], [850, 600, 1180, 1060], [0, 730, 920, 1120], [0, 1080, 700, 1400]],
  hair: [[0, 460, 520, 750], [900, 0, 1200, 570], [1140, 240, 1340, 850]],
  clothes: [[240, 0, 700, 650], [650, 0, 950, 760], [0, 280, 300, 510], [870, 620, 1160, 1040], [0, 1080, 700, 1370]],
  stockings: [[160, 740, 920, 1110]], boots: [[0, 740, 180, 1110]],
});
function jadeHexRgb(hex, fallback = '#1fe5a5') {
  const m = /^#?([\da-f]{6})$/i.exec(String(hex || ''));
  const h = m ? m[1] : fallback.slice(1);
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function jadeRgbHex(rgb) { return '#' + rgb.map(c => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, '0')).join(''); }
function jadeMixHex(a, b, amount) {
  const x = jadeHexRgb(a), y = jadeHexRgb(b);
  return jadeRgbHex(x.map((c, i) => c * (1 - amount) + y[i] * amount));
}
function jadeContrastInk(rgb) {
  const luminance = rgb.reduce((sum, c, i) => sum + (c / 255) * [0.2126, 0.7152, 0.0722][i], 0);
  return luminance > 0.48 ? '#07100d' : '#f5f7ff';
}
function resolveJadeTheme(name) {
  const preset = JADE_THEMES[name] || JADE_THEMES.jade;
  if (name === 'spectrum') {
    const accent = /^#[\da-f]{6}$/i.test(String(S.jadeSpectrum)) ? S.jadeSpectrum : JADE_THEMES.spectrum.accent;
    const rgb = jadeHexRgb(accent), highlight = jadeMixHex(accent, '#ffffff', 0.48), highlightRgb = jadeHexRgb(highlight);
    return { ...preset, accent, rgb, highlight, highlightRgb, line: jadeMixHex(accent, '#000000', 0.58), ink: jadeContrastInk(rgb), avatarStartRgb: rgb, avatarEndRgb: rgb, isGradient: false, direction: 'vertical', cssGradient: `linear-gradient(90deg, ${accent}, ${highlight})` };
  }
  if (name === 'custom-gradient') {
    const start = /^#[\da-f]{6}$/i.test(String(S.jadeGradientStart)) ? S.jadeGradientStart : JADE_THEMES['custom-gradient'].accent;
    const end = /^#[\da-f]{6}$/i.test(String(S.jadeGradientEnd)) ? S.jadeGradientEnd : JADE_THEMES['custom-gradient'].highlight;
    const direction = ['vertical', 'horizontal', 'diagonal'].includes(S.jadeGradientDirection) ? S.jadeGradientDirection : 'vertical';
    const rgb = jadeHexRgb(start), highlightRgb = jadeHexRgb(end), midpoint = rgb.map((c, i) => (c + highlightRgb[i]) / 2);
    const angle = direction === 'vertical' ? '180deg' : direction === 'horizontal' ? '90deg' : '135deg';
    return { ...preset, accent: start, rgb, highlight: end, highlightRgb, line: jadeMixHex(start, '#000000', 0.58), ink: jadeContrastInk(midpoint), avatarStartRgb: rgb, avatarEndRgb: highlightRgb, isGradient: true, direction, cssGradient: `linear-gradient(${angle}, ${start}, ${end})` };
  }
  return { ...preset, avatarStartRgb: preset.rgb, avatarEndRgb: preset.rgb, isGradient: false, direction: 'vertical', cssGradient: `linear-gradient(90deg, ${preset.accent}, ${preset.highlight})` };
}
const Jade = (() => {
  const MODEL_URL = 'https://jade.local/jadecat.model3.json';
  const UNSAFE_EVAL_SHIM = 'https://cdn.jsdelivr.net/npm/@pixi/unsafe-eval@6.5.10/dist/browser/unsafe-eval.min.js';
  const state = {
    app: null, model: null, ready: false, failed: null, host: null,
    mood: 'idle', moodUntil: 0, moodNext: 'idle',
    gaze: null, pointer: { x: 0, y: 0, t: 0 }, wander: 0,
    blink: { next: 0, t: -1 }, nodT: -1, earT: -1,
    speaking: false, mouth: 0, mouthTarget: 0, bump: 0, utterId: 0,
    cur: {}, frame: 'portrait', theme: 'jade', tintFilter: null, appearanceCanvas: null, appearanceContext: null, appearanceData: null, appearanceOriginal: null, appearanceMasks: null, appearanceTexture: null, onStatus: () => {}, onSpeak: () => {},
  };
  const P = {
    AX: 'ParamAngleX', AY: 'ParamAngleY', AZ: 'ParamAngleZ', EYE_L: 'ParamEyeLOpen', EYE_R: 'ParamEyeROpen',
    SMILE_L: 'ParamEyeLSmile', SMILE_R: 'ParamEyeRSmile', BROW_LY: 'ParamBrowLY', BROW_RY: 'ParamBrowRY',
    BROW_LA: 'ParamBrowLAngle', BROW_RA: 'ParamBrowRAngle', BROW_LF: 'ParamBrowLForm', BROW_RF: 'ParamBrowRForm',
    MOUTH_FORM: 'ParamMouthForm', MOUTH_OPEN: 'ParamMouthOpenY', CHEEK: 'ParamCheek', EAR_X: 'Param6', EAR_Y: 'Param7',
  };
  // Expression targets per mood (absolute values, blended every frame)
  const MOODS = {
    idle:      { gaze: null,          [P.BROW_LY]: 0,    [P.BROW_RY]: 0,    [P.MOUTH_FORM]: 0.25, [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'online' },
    listening: { gaze: [-0.75, -0.35], [P.BROW_LY]: 0.25, [P.BROW_RY]: 0.25, [P.MOUTH_FORM]: 0.4,  [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'listening' },
    thinking:  { gaze: [0.55, 0.65],   [P.BROW_LY]: 0.45, [P.BROW_RY]: 0.15, [P.MOUTH_FORM]: -0.2, [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'thinking' },
    writing:   { gaze: [-0.7, -0.15],  [P.BROW_LY]: 0.1,  [P.BROW_RY]: 0.1,  [P.MOUTH_FORM]: 0.3,  [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'writing' },
    working:   { gaze: [0.05, -0.85],  [P.BROW_LY]: -0.15,[P.BROW_RY]: -0.15,[P.MOUTH_FORM]: 0,    [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'jacked in' },
    happy:     { gaze: [0, 0.1],       [P.BROW_LY]: 0.35, [P.BROW_RY]: 0.35, [P.MOUTH_FORM]: 1,    [P.SMILE_L]: 1,   [P.SMILE_R]: 1,   [P.CHEEK]: 1,   label: 'done' },
    error:     { gaze: [0, -0.1],      [P.BROW_LY]: -0.8, [P.BROW_RY]: -0.8, [P.MOUTH_FORM]: -1,   [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   [P.BROW_LA]: -1, [P.BROW_RA]: -1, [P.BROW_LF]: -1, [P.BROW_RF]: -1, label: 'error' },
  };
  const EXPR_KEYS = [P.BROW_LY, P.BROW_RY, P.BROW_LA, P.BROW_RA, P.BROW_LF, P.BROW_RF, P.MOUTH_FORM, P.SMILE_L, P.SMILE_R, P.CHEEK];
  // Theme filter recolors emerald accents; the canvas texture handles separate appearance modes.
  const GEM_TINT_FRAGMENT = `
    varying vec2 vTextureCoord;
    uniform sampler2D uSampler;
    uniform vec3 uColor;
    uniform vec3 uColor2;
    uniform float uGradient;
    uniform float uDirection;
    void main(void) {
      vec4 source = texture2D(uSampler, vTextureCoord);
      vec3 rgb = source.rgb;
      float high = max(rgb.r, max(rgb.g, rgb.b));
      float low = min(rgb.r, min(rgb.g, rgb.b));
      float green = smoothstep(0.02, 0.18, rgb.g - max(rgb.r, rgb.b));
      float saturation = smoothstep(0.04, 0.25, high - low);
      float mask = green * saturation;
      float luminance = dot(rgb, vec3(0.2126, 0.7152, 0.0722));
      float gradientT = vTextureCoord.y;
      if (uDirection > 0.5) gradientT = vTextureCoord.x;
      if (uDirection > 1.5) gradientT = clamp((vTextureCoord.x + 1.0 - vTextureCoord.y) * 0.5, 0.0, 1.0);
      vec3 targetColor = mix(uColor, uColor2, gradientT * uGradient);
      float gemLuminance = max(dot(targetColor, vec3(0.2126, 0.7152, 0.0722)), 0.08);
      vec3 gem = targetColor * clamp(luminance / gemLuminance, 0.08, 1.35);
      gl_FragColor = vec4(mix(rgb, gem, mask), source.a);
    }
  `;

  const b64gz = async (id) => { if (id === 'l2d-moc') return (await fetch(AV + 'jadecat.moc3')).arrayBuffer();
    const bin = Uint8Array.from(atob(document.getElementById(id).textContent.replace(/\s+/g, '')), c => c.charCodeAt(0));
    return new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  };
  const AVF = { 'l2d-model': 'jadecat.model3.json', 'l2d-physics': 'jadecat.physics3.json', 'l2d-cdi': 'jadecat.cdi3.json' };
  const jsonOf = async (id) => (await fetch(AV + AVF[id])).json();
  const loadScript = (src) => new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error('could not load ' + src)); document.head.appendChild(s); });
  const evalAllowed = () => { try { return new Function('return 1')() === 1; } catch { return false; } };

  async function ensureEvalShim() {
    if (evalAllowed()) return;
    // Pixi 6 compiles shader sync code with new Function; under a strict CSP use the official no-eval build
    await loadScript(UNSAFE_EVAL_SHIM);
    const ue = window.PIXI?.unsafeEval || window._pixi_unsafe_eval || window.__pixi_unsafe_eval;
    try { ue?.install?.(window.PIXI); } catch {}
  }

  function setP(core, id, v) { try { core.setParameterValueById(id, v); } catch {} }
  function addP(core, id, v) { try { core.addParameterValueById(id, v); } catch {} }

  const JADE_APPEARANCE_ORDER = ['boots', 'stockings', 'hair', 'clothes'];
  const JADE_APPEARANCE_FALLBACKS = { hair: '#342c3c', clothes: '#382f47', stockings: '#252330', boots: '#20212a', skin: '#d29b7c' };
  function jadeIsSkinPixel(r, g, b) {
    const rg = r - g, rb = r - b;
    return r > 90 && rg > 4 && rg < 105 && rb > 12 && rb < 112 && g > b * 0.88;
  }
  function jadeBuildAppearanceMasks(width, height, source) {
    const sx = width / 2048, sy = height / 2048, masks = {}, claimed = new Uint8Array(width * height);
    for (const [part, boxes] of Object.entries(JADE_APPEARANCE_REGIONS)) {
      masks[part] = boxes.map(([x0, y0, x1, y1]) => ({ box: [x0 * sx, y0 * sy, x1 * sx, y1 * sy], indices: [] }));
    }
    const collect = (part, skinOnly) => {
      for (const region of masks[part]) {
        const [x0, y0, x1, y1] = region.box;
        for (let y = Math.max(0, Math.floor(y0)); y < Math.min(height, Math.ceil(y1)); y++) for (let x = Math.max(0, Math.floor(x0)); x < Math.min(width, Math.ceil(x1)); x++) {
          const pixel = y * width + x;
          if (claimed[pixel]) continue;
          const offset = pixel * 4;
          if (source[offset + 3] < 16) continue;
          const skin = jadeIsSkinPixel(source[offset], source[offset + 1], source[offset + 2]);
          if (skinOnly ? !skin : skin) continue;
          claimed[pixel] = 1; region.indices.push(offset);
        }
      }
    };
    collect('skin', true);
    for (const part of JADE_APPEARANCE_ORDER) collect(part, false);
    for (const regions of Object.values(masks)) for (const region of regions) region.indices = new Uint32Array(region.indices);
    return masks;
  }
  function jadeAppearanceSettings(part) {
    const cap = part[0].toUpperCase() + part.slice(1), modeKey = `jade${cap}Mode`;
    let mode = S[modeKey] || 'original';
    const validModes = part === 'skin' ? ['original', 'natural', 'spectrum', 'gradient'] : ['original', 'spectrum', 'gradient'];
    if (!validModes.includes(mode)) mode = 'original';
    let start = S[`jade${cap}Color`], end = S[`jade${cap}End`];
    if (part === 'skin' && mode === 'natural') start = JADE_SKIN_TONES[S.jadeSkinTone]?.color || JADE_SKIN_TONES.fair.color;
    const fallback = JADE_APPEARANCE_FALLBACKS[part];
    if (!/^#[\da-f]{6}$/i.test(String(start || ''))) start = fallback;
    if (!/^#[\da-f]{6}$/i.test(String(end || ''))) end = fallback;
    const direction = ['vertical', 'horizontal', 'diagonal'].includes(S[`jade${cap}Direction`]) ? S[`jade${cap}Direction`] : 'vertical';
    return { mode, start, end, direction };
  }
  function jadeGradientAmount(x, y, box, direction) {
    const nx = Math.max(0, Math.min(1, (x - box[0]) / Math.max(1, box[2] - box[0])));
    const ny = Math.max(0, Math.min(1, (y - box[1]) / Math.max(1, box[3] - box[1])));
    return direction === 'horizontal' ? nx : direction === 'diagonal' ? (nx + ny) * 0.5 : ny;
  }
  function setAppearance() {
    if (!state.appearanceData || !state.appearanceContext) return;
    const configs = Object.fromEntries(JADE_APPEARANCE_PARTS.map(({ id }) => [id, jadeAppearanceSettings(id)]));
    if (Object.values(configs).every(config => config.mode === 'original') && !state.appearanceMasks) return;
    if (!state.appearanceMasks) state.appearanceMasks = jadeBuildAppearanceMasks(state.appearanceCanvas.width, state.appearanceCanvas.height, state.appearanceOriginal);
    const data = state.appearanceData.data, original = state.appearanceOriginal, width = state.appearanceCanvas.width;
    data.set(original);
    for (const [part, config] of Object.entries(configs)) {
      if (config.mode === 'original') continue;
      const start = jadeHexRgb(config.start), end = jadeHexRgb(config.end), skin = part === 'skin';
      for (const { box, indices } of state.appearanceMasks[part]) for (const offset of indices) {
        const pixel = offset >>> 2, x = pixel % width, y = (pixel / width) | 0;
        const t = config.mode === 'gradient' ? jadeGradientAmount(x, y, box, config.direction) : 0;
        const tr = start[0] * (1 - t) + end[0] * t, tg = start[1] * (1 - t) + end[1] * t, tb = start[2] * (1 - t) + end[2] * t;
        const sourceLuma = (original[offset] * 0.2126 + original[offset + 1] * 0.7152 + original[offset + 2] * 0.0722) / 255;
        const targetLuma = Math.max((tr * 0.2126 + tg * 0.7152 + tb * 0.0722) / 255, 0.08);
        const shade = skin ? Math.max(0.48, Math.min(1.3, sourceLuma / 0.78)) : Math.max(0.08, Math.min(1.35, sourceLuma / targetLuma));
        data[offset] = Math.max(0, Math.min(255, Math.round(tr * shade)));
        data[offset + 1] = Math.max(0, Math.min(255, Math.round(tg * shade)));
        data[offset + 2] = Math.max(0, Math.min(255, Math.round(tb * shade)));
      }
    }
    state.appearanceContext.putImageData(state.appearanceData, 0, 0);
    state.appearanceTexture?.baseTexture?.update();
  }
  function prepareAppearanceTexture(img, url) {
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth || img.width; canvas.height = img.naturalHeight || img.height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('Canvas support is required for Jade appearance colors');
    context.drawImage(img, 0, 0, canvas.width, canvas.height);
    state.appearanceData = context.getImageData(0, 0, canvas.width, canvas.height);
    state.appearanceOriginal = new Uint8ClampedArray(state.appearanceData.data);
    state.appearanceCanvas = canvas; state.appearanceContext = context;
    state.appearanceTexture = PIXI.Texture.from(canvas);
    PIXI.Texture.addToCache(state.appearanceTexture, url);
    setAppearance();
  }

  async function init({ host, onStatus, onSpeak } = {}) {
    state.host = host; state.onStatus = onStatus || (() => {}); state.onSpeak = onSpeak || (() => {});
    if (!window.PIXI || !PIXI.live2d) throw new Error('Live2D runtime failed to load');
    state.onStatus('waking her up…');
    const files = {
      'jadecat.model3.json': await jsonOf('l2d-model'), 'jadecat.physics3.json': await jsonOf('l2d-physics'), 'jadecat.cdi3.json': await jsonOf('l2d-cdi'),
      'jadecat.moc3': await b64gz('l2d-moc'),
    };
    const L = PIXI.live2d;
    if (!L.Live2DLoader.__jade) {
      L.Live2DLoader.middlewares.unshift((ctx, next) => {
        const name = String(ctx.url).split(/[?#]/)[0].split('/').pop();
        if (Object.prototype.hasOwnProperty.call(files, name)) {
          const v = files[name]; ctx.result = v instanceof ArrayBuffer ? v.slice(0) : JSON.parse(JSON.stringify(v)); return Promise.resolve();
        }
        return next();
      });
      L.Live2DLoader.__jade = true;
    }
    // texture: decode the embedded WebP and pre-seed PIXI's cache under the URL the model will resolve
    const img = new Image(); img.src = AV + 'texture.webp';
    await img.decode();
    const texUrl = new URL(files['jadecat.model3.json'].FileReferences.Textures[0], MODEL_URL).href;
    try { prepareAppearanceTexture(img, texUrl); } catch (e) { console.warn('Jade appearance color setup unavailable', e); if (!PIXI.utils.TextureCache[texUrl]) PIXI.Texture.addToCache(PIXI.Texture.from(img), texUrl); }

    let app;
    const mk = () => new PIXI.Application({ resizeTo: host, backgroundAlpha: 0, antialias: true, autoDensity: true, resolution: Math.min(devicePixelRatio || 1, 2) });
    try { app = mk(); } catch (e) {
      if (!/unsafe-eval/i.test(String(e?.message))) throw e;
      await ensureEvalShim(); app = mk();
    }
    app.view.className = 'jade-canvas';
    host.prepend(app.view);
    state.app = app;
    const model = await L.Live2DModel.from(MODEL_URL, { autoInteract: false, autoUpdate: true });
    state.model = model;
    model.visible = false;
    app.stage.addChild(model);
    try {
      state.tintFilter = new PIXI.Filter(undefined, GEM_TINT_FRAGMENT, { uColor: JADE_THEMES.jade.rgb.map(c => c / 255), uColor2: JADE_THEMES.jade.rgb.map(c => c / 255), uGradient: 0, uDirection: 0 });
      state.tintFilter.padding = 2; model.filters = [state.tintFilter];
    } catch (e) { state.tintFilter = null; console.warn('Jade gem-color filter unavailable', e); }
    setTheme(S.jadeTheme);
    const core = model.internalModel.coreModel;
    for (const k of EXPR_KEYS) state.cur[k] = 0;
    model.internalModel.on('beforeModelUpdate', () => frame(core));
    app.ticker.addOnce(() => { layout(); model.visible = true; });
    new ResizeObserver(() => { app.resize(); layout(); }).observe(host);
    window.addEventListener('pointermove', e => { state.pointer = { x: e.clientX, y: e.clientY, t: performance.now() }; }, { passive: true });
    state.ready = true; state.onStatus('');
    return api;
  }

  function layout() {
    const { app, model } = state; if (!model) return;
    model.scale.set(1);
    const b = model.getLocalBounds(); const w = app.renderer.width / app.renderer.resolution, h = app.renderer.height / app.renderer.resolution;
    if (!w || !h) return;
    if (h < 130) {
      // compact strip: just her face, parked on the left
      const viewH = b.height * 0.155;
      const s = h / viewH;
      model.scale.set(s); model.pivot.set(b.x + b.width / 2, b.y + b.height * 0.012); model.position.set(Math.max(40, h * 0.55), 0);
    } else if (state.frame === 'full') {
      const s = Math.min(w * 0.92 / b.width, h * 0.94 / b.height);
      model.scale.set(s); model.pivot.set(b.x + b.width / 2, b.y + b.height / 2); model.position.set(w / 2, h / 2 + h * 0.01);
    } else {
      // portrait: head + upper body fills the stage, cropped below the chest
      const viewH = b.height * 0.44, viewW = b.width * 0.66;
      const s = Math.min(h / viewH, w / viewW);
      model.scale.set(s); model.pivot.set(b.x + b.width / 2, b.y); model.position.set(w / 2, h * 0.035);
    }
  }

  function frame(core) {
    const now = performance.now();
    if (state.mood !== 'idle' && state.moodUntil && now > state.moodUntil) { state.mood = state.moodNext; state.moodUntil = 0; }
    const M = MOODS[state.mood] || MOODS.idle;
    // gaze: mood target > recent pointer > lazy wander
    const fc = state.model.internalModel.focusController;
    let gx, gy;
    if (state.speaking) { gx = 0; gy = 0.05; }
    else if (M.gaze) { [gx, gy] = M.gaze; gx += Math.sin(now / 900) * 0.05; }
    else if (now - state.pointer.t < 4000) {
      const r = state.app.view.getBoundingClientRect();
      gx = Math.max(-1, Math.min(1, (state.pointer.x - (r.left + r.width / 2)) / (window.innerWidth * 0.35)));
      gy = Math.max(-1, Math.min(1, -(state.pointer.y - (r.top + r.height * 0.3)) / (window.innerHeight * 0.4)));
    } else { state.wander += 0.006; gx = Math.sin(state.wander) * 0.35; gy = Math.cos(state.wander * 0.6) * 0.15; }
    fc.focus(gx, gy);
    // expressions (smoothed)
    for (const k of EXPR_KEYS) { const t = M[k] ?? 0; state.cur[k] += (t - state.cur[k]) * 0.12; setP(core, k, state.cur[k]); }
    // blink (the model has no EyeBlink group, so we drive it)
    const B = state.blink; let open = 1;
    if (B.t < 0 && now > B.next) B.t = now;
    if (B.t >= 0) { const d = now - B.t; open = d < 70 ? 1 - d / 70 : d < 110 ? 0 : d < 230 ? (d - 110) / 120 : 1; if (d >= 230) { B.t = -1; B.next = now + 1800 + Math.random() * 4200 * (Math.random() < 0.15 ? 0.1 : 1); } }
    const smile = Math.max(state.cur[P.SMILE_L], 0);
    setP(core, P.EYE_L, open * (1 - smile * 0.35)); setP(core, P.EYE_R, open * (1 - smile * 0.35));
    // mouth: speech lipsync or closed
    if (state.speaking) { const n = 0.5 + 0.5 * Math.sin(now / 55) * Math.sin(now / 137 + 1.3); state.mouthTarget = 0.18 + 0.62 * n + state.bump; state.bump *= 0.86; }
    else state.mouthTarget = state.mood === 'happy' ? 0.25 : 0;
    state.mouth += (state.mouthTarget - state.mouth) * (state.mouthTarget > state.mouth ? 0.55 : 0.25);
    setP(core, P.MOUTH_OPEN, Math.min(1, state.mouth));
    for (const [k, v] of Object.entries(state.extra || {})) setP(core, k, v);
    // nod + ear twitch (transient)
    if (state.nodT >= 0) { const d = (now - state.nodT) / 600; if (d > 1) state.nodT = -1; else addP(core, P.AY, -Math.sin(d * Math.PI) * 14); }
    if (state.earT < 0 && Math.random() < (state.mood === 'working' ? 0.012 : 0.002)) state.earT = now;
    if (state.earT >= 0) { const d = (now - state.earT) / 260; if (d > 1) state.earT = -1; else { addP(core, P.EAR_X, Math.sin(d * Math.PI * 2) * 0.8); addP(core, P.EAR_Y, Math.sin(d * Math.PI) * 0.6); } }
  }

  // ---------------- public api ----------------
  function setMood(m, ms = 0, then = 'idle') {
    if (!MOODS[m]) m = 'idle';
    state.mood = m; state.moodUntil = ms ? performance.now() + ms : 0; state.moodNext = then;
  }
  function nod() { state.nodT = performance.now(); }
  function setTheme(name) {
    state.theme = Object.prototype.hasOwnProperty.call(JADE_THEMES, name) ? name : 'jade';
    const config = resolveJadeTheme(state.theme), uniforms = state.tintFilter?.uniforms;
    if (!uniforms) return;
    const set = (key, value) => {
      const current = uniforms[key];
      if (current?.set && Array.isArray(value)) current.set(value);
      else uniforms[key] = Array.isArray(value) ? new Float32Array(value) : value;
    };
    set('uColor', config.avatarStartRgb.map(c => c / 255));
    set('uColor2', config.avatarEndRgb.map(c => c / 255));
    set('uGradient', config.isGradient ? 1 : 0);
    set('uDirection', config.direction === 'horizontal' ? 1 : config.direction === 'diagonal' ? 2 : 0);
  }
  function setFrame(f) { state.frame = f === 'full' ? 'full' : 'portrait'; layout(); }

  // speech (Web Speech API) with procedural lipsync
  let voices = [];
  const loadVoices = () => { try { voices = speechSynthesis.getVoices(); } catch { voices = []; } return voices; };
  if (!!window.speechSynthesis) { loadVoices(); speechSynthesis.onvoiceschanged = loadVoices; }
  function pickVoice(name) {
    const v = voices.length ? voices : loadVoices(); if (!v.length) return null;
    if (name) { const f = v.find(x => x.name === name); if (f) return f; }
    const pref = [/Libby|Sonia|Maisie|Mia|Hazel/i, /Google UK English Female/i, /Serena|Kate|Martha|Stephanie/i, /en-GB/i, /Female|Samantha|Zira|Aria|Jenny/i, /^en/i];
    for (const re of pref) { const f = v.find(x => re.test(x.name) || re.test(x.lang)); if (f) return f; }
    return v[0];
  }
  function speechText(md) {
    return String(md || '')
      .replace(/```[\s\S]*?```/g, '\n(code below)\n').replace(/`([^`]+)`/g, '$1')
      .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (m, a, b) => b || a)
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/^#+\s*/gm, '').replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '').replace(/[*_~>|#]/g, '')
      .replace(/https?:\/\/\S+/g, 'a link')
      .replace(/([^.!?:;,\s])[ \t]*\n+/g, '$1. ')          // line / list-item ends become pauses
      .replace(/\s+/g, ' ').trim();
  }
  // Split into short pieces: browsers silently stop long utterances (~15 s) and some drop big queues.
  function chunkText(t, max = 180) {
    const out = [];
    for (let s of t.match(/[^.!?…]+[.!?…]*["')\]]*\s*/g) || [t]) {
      s = s.trim(); if (!s) continue;
      while (s.length > max) {
        let cut = s.lastIndexOf(', ', max); if (cut < max * 0.4) cut = s.lastIndexOf(' ', max); if (cut < max * 0.4) cut = max;
        out.push(s.slice(0, cut + 1).trim()); s = s.slice(cut + 1).trim();
      }
      if (s) out.push(s);
    }
    return out;
  }
  // Native TTS bridge (the Android app exposes window.NetrunnerAndroid; WebView has no speechSynthesis)
  const nativeTTS = () => typeof window.NetrunnerAndroid?.speak === 'function';
  const nativeCbs = new Map(); let nativeSeq = 0;
  window.__jadeTTS = (ev, uid) => { const cb = nativeCbs.get(uid); if (cb) cb(ev); };
  function speakChunk(c, v, rate, pitch, onstart, ondone) {
    if (nativeTTS()) {
      const uid = 'u' + (++nativeSeq); let started = false;
      nativeCbs.set(uid, ev => { if (ev === 'start' && !started) { started = true; onstart(); } else if (ev !== 'start') { nativeCbs.delete(uid); ondone(); } });
      try { window.NetrunnerAndroid.speak(uid, c, pitch, rate); } catch { nativeCbs.delete(uid); ondone(); }
      return;
    }
    const u = new SpeechSynthesisUtterance(c); if (v) { u.voice = v; u.lang = v.lang; } u.rate = rate; u.pitch = pitch;
    let done = false; const fin = () => { if (!done) { done = true; clearTimeout(guard); ondone(); } };
    u.onstart = onstart; u.onboundary = () => { state.bump = 0.35; }; u.onend = fin; u.onerror = fin;
    // safety net: if the engine never fires onend, move on after a generous estimate
    const guard = setTimeout(fin, 4000 + c.length * 140 / Math.max(rate, 0.3));
    speechSynthesis.speak(u);
  }
  function speak(text, { voice, rate = 1.03, pitch = 1.25 } = {}) {
    if (!(!!window.speechSynthesis) && !nativeTTS()) return false;
    const t = speechText(text); if (!t) return false;
    stop();
    const id = ++state.utterId; const v = nativeTTS() ? null : pickVoice(voice);
    const chunks = chunkText(t); let i = 0;
    const next = () => {
      if (id !== state.utterId) return;
      if (i >= chunks.length) { state.speaking = false; state.onSpeak(''); return; }
      const c = chunks[i++];
      speakChunk(c, v, rate, pitch, () => { if (id === state.utterId) { state.speaking = true; state.onSpeak(c); } }, next);
    };
    next();
    return true;
  }
  function stop() {
    state.utterId++; state.speaking = false;
    try { speechSynthesis.cancel(); } catch {}
    if (nativeTTS()) { try { window.NetrunnerAndroid.stop(); } catch {} nativeCbs.clear(); }
  }

  const api = { init, setMood, nod, setFrame, setTheme, setAppearance, speak, stop, voices: () => (voices.length ? voices : loadVoices()), get ready() { return state.ready; }, get mood() { return state.mood; }, label: () => (MOODS[state.mood] || MOODS.idle).label, state, P };
  return api;
})();

function renderJadeThemePicker() {
  const host = $('#jadeThemePicker'); if (!host) return;
  host.innerHTML = Object.entries(JADE_THEMES).map(([id, theme]) => {
    const kind = id === 'spectrum' ? ' spectrum' : id === 'custom-gradient' ? ' custom-gradient' : '';
    return `<button type="button" class="gem-theme" data-jade-theme="${esc(id)}" aria-pressed="${String(id === S.jadeTheme)}" title="Use the ${esc(theme.name)} theme"><span class="gem-theme-swatch${kind}" style="--swatch:${theme.accent}" aria-hidden="true"></span><span>${esc(theme.name)}</span></button>`;
  }).join('');
  host.querySelectorAll('[data-jade-theme]').forEach(button => {
    button.onclick = () => applyJadeTheme(button.dataset.jadeTheme, true);
  });
  const bindColor = (selector, key, mode) => {
    const input = $(selector); if (!input) return;
    input.value = S[key];
    input.oninput = () => { S[key] = input.value; if (S.jadeTheme === mode) applyJadeTheme(mode); };
    input.onchange = () => { S[key] = input.value; if (S.jadeTheme === mode) applyJadeTheme(mode, true); else saveSettings(); };
  };
  bindColor('#jadeSpectrumColor', 'jadeSpectrum', 'spectrum');
  bindColor('#jadeGradientStart', 'jadeGradientStart', 'custom-gradient');
  bindColor('#jadeGradientEnd', 'jadeGradientEnd', 'custom-gradient');
  const direction = $('#jadeGradientDirection');
  if (direction) { direction.value = S.jadeGradientDirection || 'vertical'; direction.onchange = () => { S.jadeGradientDirection = direction.value; applyJadeTheme('custom-gradient', true); }; }
}
function applyJadeTheme(name, persist = false) {
  const id = Object.prototype.hasOwnProperty.call(JADE_THEMES, name) ? name : 'jade';
  S.jadeTheme = id;
  const theme = resolveJadeTheme(id), root = document.documentElement;
  root.style.setProperty('--yl', theme.accent);
  root.style.setProperty('--cy', theme.highlight);
  root.style.setProperty('--line-hot', theme.line);
  root.style.setProperty('--theme-rgb', theme.rgb.join(','));
  root.style.setProperty('--theme-highlight-rgb', theme.highlightRgb.join(','));
  root.style.setProperty('--theme-ink', theme.ink);
  root.style.setProperty('--theme-gradient', theme.cssGradient);
  document.body.dataset.jadeTheme = id;
  { const _e = $('#jadeSpectrumControls'); if (_e) _e.hidden = id !== 'spectrum'; }
  { const _e = $('#jadeGradientControls'); if (_e) _e.hidden = id !== 'custom-gradient'; }
  const label = $('#btnTheme'); if (label) label.textContent = theme.name;
  $('#jadeThemePicker')?.querySelectorAll('[data-jade-theme]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.jadeTheme === id));
  });
  Jade.setTheme(id);
  if (persist) saveSettings();
}
function renderJadeAppearancePicker() {
  const host = $('#jadeAppearancePicker'); if (!host) return;
  const modes = [['original', 'Original'], ['spectrum', 'Spectrum color'], ['gradient', 'Gradient']];
  host.innerHTML = JADE_APPEARANCE_PARTS.map(({ id, label }) => {
    const cap = id[0].toUpperCase() + id.slice(1), skin = id === 'skin';
    const options = skin ? [['original', 'Original'], ['natural', 'Natural tone'], ...modes.slice(1)] : modes;
    return `<fieldset class="jade-appearance-card" data-appearance-card="${id}"><legend>${label}</legend>
      <label class="appearance-field" for="jadeAppearance${cap}Mode"><span class="lbl">Color mode</span><select class="field" id="jadeAppearance${cap}Mode" data-appearance-mode="${id}">${options.map(([value, text]) => `<option value="${value}">${text}</option>`).join('')}</select></label>
      ${skin ? `<label class="appearance-field" data-appearance-natural hidden for="jadeSkinTone"><span class="lbl">Natural tone</span><select class="field" id="jadeSkinTone" data-appearance-tone>${Object.entries(JADE_SKIN_TONES).map(([value, tone]) => `<option value="${value}">${tone.label}</option>`).join('')}</select></label>` : ''}
      <label class="appearance-field" data-appearance-start hidden for="jadeAppearance${cap}Color"><span class="lbl">Spectrum color</span><input type="color" class="theme-color-input" id="jadeAppearance${cap}Color" data-appearance-input="jade${cap}Color"></label>
      <div class="appearance-gradient-fields" data-appearance-gradient hidden><label class="appearance-field" for="jadeAppearance${cap}End"><span class="lbl">Gradient end</span><input type="color" class="theme-color-input" id="jadeAppearance${cap}End" data-appearance-input="jade${cap}End"></label><label class="appearance-field" for="jadeAppearance${cap}Direction"><span class="lbl">Direction</span><select class="field" id="jadeAppearance${cap}Direction" data-appearance-direction="${id}"><option value="vertical">Vertical</option><option value="horizontal">Horizontal</option><option value="diagonal">Diagonal</option></select></label></div>
      <p class="hint appearance-help">Shading and texture detail are preserved.</p></fieldset>`;
  }).join('');
  const sync = card => {
    const part = card.dataset.appearanceCard, mode = card.querySelector('[data-appearance-mode]').value;
    const natural = card.querySelector('[data-appearance-natural]'), start = card.querySelector('[data-appearance-start]'), gradient = card.querySelector('[data-appearance-gradient]');
    if (natural) natural.hidden = mode !== 'natural';
    if (start) { start.hidden = mode !== 'spectrum' && mode !== 'gradient'; start.querySelector('.lbl').textContent = mode === 'gradient' ? 'Gradient start' : 'Spectrum color'; }
    if (gradient) gradient.hidden = mode !== 'gradient';
  };
  host.querySelectorAll('[data-appearance-card]').forEach(card => {
    const part = card.dataset.appearanceCard, cap = part[0].toUpperCase() + part.slice(1), mode = card.querySelector('[data-appearance-mode]');
    mode.value = S[`jade${cap}Mode`] || 'original';
    card.querySelectorAll('[data-appearance-input]').forEach(input => { input.value = S[input.dataset.appearanceInput];
      input.oninput = () => { S[input.dataset.appearanceInput] = input.value; if (mode.value === 'spectrum' || mode.value === 'gradient') Jade.setAppearance(); };
      input.onchange = () => { S[input.dataset.appearanceInput] = input.value; Jade.setAppearance(); saveSettings(); };
    });
    const tone = card.querySelector('[data-appearance-tone]');
    if (tone) { tone.value = S.jadeSkinTone || 'fair'; tone.onchange = () => { S.jadeSkinTone = tone.value; if (mode.value === 'natural') Jade.setAppearance(); saveSettings(); }; }
    const direction = card.querySelector('[data-appearance-direction]');
    if (direction) { direction.value = S[`jade${cap}Direction`] || 'vertical'; direction.onchange = () => { S[`jade${cap}Direction`] = direction.value; if (mode.value === 'gradient') Jade.setAppearance(); saveSettings(); }; }
    mode.onchange = () => { S[`jade${cap}Mode`] = mode.value; sync(card); Jade.setAppearance(); saveSettings(); };
    sync(card);
  });
}

window.Jade = Jade; Object.assign(window, { JADE_THEMES, resolveJadeTheme, applyJadeTheme, renderJadeThemePicker, renderJadeAppearancePicker });
