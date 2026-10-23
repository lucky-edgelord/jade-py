# Third-party notices

The dashboard (src/jade/web/avatar/) ships Jade's avatar, taken from the Jade Vault app:

| Component | Version | License | Notes |
|---|---|---|---|
| PixiJS | 6.5.10 | MIT | Live2D renderer. |
| pixi-live2d-display | 0.4.0 | MIT | Live2D model loader. |
| Live2D Cubism Core for Web | 4.x | **Live2D Proprietary Software License** | Redistributable inside apps under Live2D's terms: https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html . Publishers above Live2D's revenue threshold need a Live2D publication license. |
| Jade avatar ("jadecat" Live2D model and texture) | — | Owner's own asset | Not covered by the MIT license. Do not reuse without the owner's permission. |

The Jade Link app (android/) is built with Apktool, uber-apk-signer and dx (from dex2jar), which are downloaded at
build time and not part of the app.
