# View layout libraries

Vinc uses the unmodified official npm packages pinned in `app/package-lock.json`.
The package lock records each archive URL and SHA512 integrity value.

| Library | Version | Use | License and source |
| --- | --- | --- | --- |
| elkjs | 0.12.0 | Temporal node placement and orthogonal sections | EPL 2.0; `elkjs-LICENSE.md`; https://github.com/kieler/elkjs |
| libavoid-js | 0.4.5 | Cross topic routing at fixed coordinates | LGPL 2.1 or later; `libavoid-js-LICENSE.txt`; https://github.com/Aksem/libavoid-js and https://github.com/mjwybrow/adaptagrams |

The libavoid WASM remains a separate, replaceable asset. To replace or rebuild
these libraries, install the desired package in `app`, update its lock file,
and run `npm run build`. The worker imports the official package files; Vinc
does not patch their implementations. The npm archives and source repositories
are available without a paid subscription. The license files ship with the app
at `third-party/` under its deployed base URL.
