/**
 * PWA install/detection helpers, shared by the install prompt and the
 * push permission prompt.
 *
 * Three discoverable states matter:
 *
 *   1. Standalone — the page is running in PWA mode (launched from the
 *      home screen). On iOS Safari this is the only context where
 *      browser push permission can be requested at all; on Android
 *      Chrome both standalone and tab modes work, but standalone is
 *      what we're driving users toward.
 *   2. Mobile Safari on iOS — the install flow is manual ("share →
 *      Add to Home Screen"). Apple does not expose the Web App Install
 *      banner API to Safari; we must show instructions.
 *   3. Android Chrome (or any Chromium-based mobile browser) — the
 *      install flow is programmatic via the `beforeinstallprompt`
 *      event, which Chrome fires automatically when its install
 *      heuristics decide the site is install-worthy.
 *
 * Every function here treats SSR (`typeof window === "undefined"`) as
 * "false." None of these checks are usable during prerender; the
 * components that consume them gate behavior behind a useEffect to
 * stay SSR-safe.
 */

/** True when the page is running as an installed PWA. */
export function isAppInstalled(): boolean {
  if (typeof window === "undefined") return false;
  if (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(display-mode: standalone)").matches
  ) {
    return true;
  }
  // iOS Safari exposes its standalone state through a non-standard
  // boolean on `navigator`. Type-cast since TS doesn't know about it.
  const navStandalone = (navigator as Navigator & { standalone?: boolean })
    .standalone;
  return navStandalone === true;
}

/** True for iPhone, iPad, or iPod running Mobile Safari. */
export function isMobileSafari(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  // iPad on iPadOS 13+ presents as Macintosh; differentiate by touch
  // points to catch the modern iPad-as-desktop UA string.
  const isIosDevice =
    /iPad|iPhone|iPod/.test(ua) ||
    (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
  if (!isIosDevice) return false;
  // Exclude in-app browsers (Chrome iOS, Firefox iOS) — they cannot
  // add to home screen and trigger the share-sheet flow themselves.
  return !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
}

/** True for Chrome (or another Chromium browser) on Android. */
export function isAndroidChrome(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (!/Android/.test(ua)) return false;
  // Excludes Firefox Android (which uses a Gecko UA without "Chrome")
  // but accepts Edge / Samsung Internet, all of which fire
  // beforeinstallprompt.
  return /Chrome|CriOS|EdgA|SamsungBrowser/.test(ua);
}

/** True if either of the two install-capable mobile browsers. */
export function isMobileBrowserInstallable(): boolean {
  return isMobileSafari() || isAndroidChrome();
}

/**
 * The shape Chromium browsers use for the deferred install prompt.
 * Not in lib.dom.d.ts as of TypeScript 5.x, so declared inline.
 */
export interface BeforeInstallPromptEvent extends Event {
  readonly platforms: readonly string[];
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}
