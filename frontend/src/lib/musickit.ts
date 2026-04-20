/**
 * MusicKit JS loader + authorize helper.
 *
 * Apple Music does not use an OAuth redirect — the user consents in a
 * MusicKit-managed overlay inside our own page. This module is the
 * only place that touches the `window.MusicKit` global so the rest of
 * the app can stay TypeScript-clean.
 *
 * The script is loaded lazily (on first call to {@link loadMusicKit})
 * rather than globally — no need to pay the payload on pages that
 * never connect Apple Music.
 */

const MUSICKIT_SRC = "https://js-cdn.music.apple.com/musickit/v3/musickit.js";

interface MusicKitInstance {
  authorize(): Promise<string>;
  unauthorize(): Promise<void>;
}

interface MusicKitConfig {
  developerToken: string;
  app: { name: string; build: string };
}

interface MusicKitStatic {
  configure(config: MusicKitConfig): Promise<MusicKitInstance>;
  getInstance(): MusicKitInstance;
}

declare global {
  interface Window {
    MusicKit?: MusicKitStatic;
  }
}

let loadPromise: Promise<MusicKitStatic> | null = null;

/**
 * Inject the MusicKit JS script tag and resolve once `window.MusicKit`
 * is available. Idempotent — subsequent calls return the same promise.
 */
export function loadMusicKit(): Promise<MusicKitStatic> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("MusicKit is only available in the browser."));
  }
  if (window.MusicKit) {
    return Promise.resolve(window.MusicKit);
  }
  if (loadPromise) {
    return loadPromise;
  }

  loadPromise = new Promise<MusicKitStatic>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${MUSICKIT_SRC}"]`,
    );
    let settled = false;
    const settle = (fn: () => void): void => {
      if (settled) return;
      settled = true;
      fn();
    };
    const onReady = (): void => {
      if (window.MusicKit) {
        settle(() => resolve(window.MusicKit as MusicKitStatic));
      } else {
        settle(() =>
          reject(new Error("musickitloaded fired but window.MusicKit is missing.")),
        );
      }
    };
    document.addEventListener("musickitloaded", onReady, { once: true });

    // Some browsers fire script.onload before dispatching `musickitloaded`,
    // and MusicKit v3 occasionally attaches to `window.MusicKit` synchronously
    // — poll briefly after onload so we resolve even if we miss the event.
    const pollForGlobal = (attemptsLeft: number): void => {
      if (window.MusicKit) {
        settle(() => resolve(window.MusicKit as MusicKitStatic));
        return;
      }
      if (attemptsLeft <= 0 || settled) return;
      window.setTimeout(() => pollForGlobal(attemptsLeft - 1), 50);
    };

    if (!existing) {
      const script = document.createElement("script");
      script.src = MUSICKIT_SRC;
      script.async = true;
      script.onload = () => pollForGlobal(40);
      script.onerror = () =>
        settle(() =>
          reject(
            new Error(
              "Failed to load musickit.js — check the browser console for CSP or network errors.",
            ),
          ),
        );
      document.head.appendChild(script);
    } else {
      pollForGlobal(40);
    }
  });
  return loadPromise;
}

/**
 * Load MusicKit, configure it with a fresh developer token, and prompt
 * the user for consent. Returns the Music User Token (MUT) the backend
 * needs to finish the connect flow.
 */
export async function authorizeAppleMusic({
  developerToken,
  appName,
  appBuild,
}: {
  developerToken: string;
  appName: string;
  appBuild: string;
}): Promise<string> {
  const MusicKit = await loadMusicKit();
  const instance = await MusicKit.configure({
    developerToken,
    app: { name: appName, build: appBuild },
  });
  const mut = await instance.authorize();
  if (typeof mut !== "string" || !mut) {
    throw new Error("Apple Music did not return a user token.");
  }
  return mut;
}
