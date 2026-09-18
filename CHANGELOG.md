# Changelog

## [0.5.4](https://github.com/andrewtryder/ha-herohealth/compare/v0.5.3...v0.5.4) (2026-09-18)


### Bug Fixes

* make scheduled-dose refresh authoritative ([#26](https://github.com/andrewtryder/ha-herohealth/issues/26)) ([425ea2b](https://github.com/andrewtryder/ha-herohealth/commit/425ea2b25e1fe11a3ce1e671e24f2ea03077b1c9))

## [0.5.3](https://github.com/andrewtryder/ha-herohealth/compare/v0.5.2...v0.5.3) (2026-09-17)


### Bug Fixes

* keep scheduled-dose timers on event loop and queue pending boundaries ([#24](https://github.com/andrewtryder/ha-herohealth/issues/24)) ([b232830](https://github.com/andrewtryder/ha-herohealth/commit/b2328306237907f74137f8ea8977186f88ff6141))

## [0.5.2](https://github.com/andrewtryder/ha-herohealth/compare/v0.5.1...v0.5.2) (2026-09-15)


### Bug Fixes

* close dispense authorization and timezone safety gaps ([#20](https://github.com/andrewtryder/ha-herohealth/issues/20)) ([af038c5](https://github.com/andrewtryder/ha-herohealth/commit/af038c53dc8dfd31ad795dd4c6297986d2e97b15))

## [0.5.1](https://github.com/andrewtryder/ha-herohealth/compare/v0.5.0...v0.5.1) (2026-09-14)


### Bug Fixes

* refresh scheduled-dose eligibility at dose time ([#18](https://github.com/andrewtryder/ha-herohealth/issues/18)) ([fa0fb31](https://github.com/andrewtryder/ha-herohealth/commit/fa0fb31abc73f7bf9abaa3cb75235ebbda7b5934))

## [0.5.0](https://github.com/andrewtryder/ha-herohealth/compare/v0.4.1...v0.5.0) (2026-09-12)


### Features

* harden remote dispense safety, lifecycle, and HA 2026 architecture ([#16](https://github.com/andrewtryder/ha-herohealth/issues/16)) ([cd0a23b](https://github.com/andrewtryder/ha-herohealth/commit/cd0a23b605bc8d5cc275721d07389bb979033309))

## [0.4.1](https://github.com/andrewtryder/ha-herohealth/compare/v0.4.0...v0.4.1) (2026-09-12)


### Bug Fixes

* configure semantic PR title types correctly ([2e2ba69](https://github.com/andrewtryder/ha-herohealth/commit/2e2ba698c9f6b7ba3c79ca01186ffc4d38b95026))
* configure semantic PR title types correctly ([658e3c5](https://github.com/andrewtryder/ha-herohealth/commit/658e3c519764e51310b37e4aa583bed0a86230ad))

## [0.4.0](https://github.com/andrewtryder/ha-herohealth/compare/v0.3.0...v0.4.0) (2026-09-11)


### Features

* add medication dashboard and dispense button ([85f96fc](https://github.com/andrewtryder/ha-herohealth/commit/85f96fc5e4f4ff1a121c93b090a95b685c0d537a))

## [0.3.0](https://github.com/andrewtryder/ha-herohealth/compare/v0.2.0...v0.3.0) (2026-09-05)


### Features

* hero device metadata and recurring schedule fallback ([#10](https://github.com/andrewtryder/ha-herohealth/issues/10)) ([461b300](https://github.com/andrewtryder/ha-herohealth/commit/461b300b718754eef73af02f3b0d8795e3e68bfc))


### Bug Fixes

* close Hero dispense safety gaps ([56aee01](https://github.com/andrewtryder/ha-herohealth/commit/56aee01a3316c8787501ed42ea229c8f1657fb59))
* harden Hero safety and reliability ([39a03be](https://github.com/andrewtryder/ha-herohealth/commit/39a03be63f22cc33542067c7f0ab487702be7f75))
* harden Hero safety and reliability ([8c31807](https://github.com/andrewtryder/ha-herohealth/commit/8c3180713f53760fe60c7372257c6465194a731d))

## [0.2.0](https://github.com/andrewtryder/ha-herohealth/compare/v0.1.2...v0.2.0) (2026-09-03)


### Features

* add dispense availability controls ([2c5534d](https://github.com/andrewtryder/ha-herohealth/commit/2c5534d8253ec6701afb5bd0d7bba9a04f644f53))


### Bug Fixes

* bind Hero sessions to configured identity ([bcd0ddc](https://github.com/andrewtryder/ha-herohealth/commit/bcd0ddcbb4f74614b3644b4feaf63f9e464d85ec))
* harden Hero authentication recovery ([b5d6190](https://github.com/andrewtryder/ha-herohealth/commit/b5d619081972d3dc707a7fde4bda392356fa9be0))

## [0.1.2](https://github.com/andrewtryder/ha-herohealth/compare/v0.1.1...v0.1.2) (2026-09-03)


### Bug Fixes

* await Home Assistant service actions ([ce54420](https://github.com/andrewtryder/ha-herohealth/commit/ce5442050a736d4bad099d1971094c26bc30d97c))

## [0.1.1](https://github.com/andrewtryder/ha-herohealth/compare/v0.1.0...v0.1.1) (2026-09-03)


### Bug Fixes

* finalize custom integration validation ([816540d](https://github.com/andrewtryder/ha-herohealth/commit/816540d97dcaeb7db658799db4409629544ce91e))
* harden pre-live Home Assistant behavior ([8e89e88](https://github.com/andrewtryder/ha-herohealth/commit/8e89e88f2fd4ed7fcac27e8e70ddf3a98600cd27))
* route coordinator polling through authenticated session ([cdeb4f9](https://github.com/andrewtryder/ha-herohealth/commit/cdeb4f9f90ca2720bc9128273cec9f90623d496f))
