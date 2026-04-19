/**
 * WebAuthn ceremony helpers for the browser.
 *
 * The backend emits public-key options from py_webauthn's
 * ``options_to_json``, which encodes ``challenge``, ``user.id``, and any
 * ``allowCredentials[].id`` / ``excludeCredentials[].id`` as base64url
 * strings. ``navigator.credentials.create``/``get`` need those same
 * fields decoded to ``BufferSource`` instances, so we normalize the
 * options on the way into the platform and normalize the returned
 * credential back to base64url JSON before posting to the backend.
 */
export interface PublicKeyCredentialCreationOptionsJSON {
  rp: { id?: string; name: string };
  user: { id: string; name: string; displayName: string };
  challenge: string;
  pubKeyCredParams: Array<{ type: "public-key"; alg: number }>;
  timeout?: number;
  excludeCredentials?: Array<{
    type: "public-key";
    id: string;
    transports?: AuthenticatorTransport[];
  }>;
  authenticatorSelection?: AuthenticatorSelectionCriteria;
  attestation?: AttestationConveyancePreference;
  extensions?: AuthenticationExtensionsClientInputs;
}

export interface PublicKeyCredentialRequestOptionsJSON {
  challenge: string;
  timeout?: number;
  rpId?: string;
  allowCredentials?: Array<{
    type: "public-key";
    id: string;
    transports?: AuthenticatorTransport[];
  }>;
  userVerification?: UserVerificationRequirement;
  extensions?: AuthenticationExtensionsClientInputs;
}

export interface RegistrationCredentialJSON {
  id: string;
  rawId: string;
  type: "public-key";
  authenticatorAttachment?: AuthenticatorAttachment | null;
  response: {
    clientDataJSON: string;
    attestationObject: string;
    transports?: string[];
  };
  clientExtensionResults: AuthenticationExtensionsClientOutputs;
}

export interface AuthenticationCredentialJSON {
  id: string;
  rawId: string;
  type: "public-key";
  authenticatorAttachment?: AuthenticatorAttachment | null;
  response: {
    clientDataJSON: string;
    authenticatorData: string;
    signature: string;
    userHandle: string | null;
  };
  clientExtensionResults: AuthenticationExtensionsClientOutputs;
}

function base64UrlToBuffer(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer as ArrayBuffer;
}

function bufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i] as number);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Convert the backend's JSON registration options into the native
 * ``PublicKeyCredentialCreationOptions`` shape WebAuthn expects.
 */
export function decodeRegistrationOptions(
  options: PublicKeyCredentialCreationOptionsJSON,
): PublicKeyCredentialCreationOptions {
  return {
    ...options,
    challenge: base64UrlToBuffer(options.challenge),
    user: {
      ...options.user,
      id: base64UrlToBuffer(options.user.id),
    },
    excludeCredentials: options.excludeCredentials?.map((cred) => ({
      ...cred,
      id: base64UrlToBuffer(cred.id),
    })),
  };
}

/**
 * Convert the backend's JSON authentication options into the native
 * ``PublicKeyCredentialRequestOptions`` shape.
 */
export function decodeAuthenticationOptions(
  options: PublicKeyCredentialRequestOptionsJSON,
): PublicKeyCredentialRequestOptions {
  return {
    ...options,
    challenge: base64UrlToBuffer(options.challenge),
    allowCredentials: options.allowCredentials?.map((cred) => ({
      ...cred,
      id: base64UrlToBuffer(cred.id),
    })),
  };
}

function transportsFromResponse(response: AuthenticatorAttestationResponse): string[] {
  const maybe = response as AuthenticatorAttestationResponse & {
    getTransports?: () => string[];
  };
  if (typeof maybe.getTransports === "function") {
    try {
      return maybe.getTransports();
    } catch {
      return [];
    }
  }
  return [];
}

/**
 * Serialize a freshly-minted attestation credential into the JSON
 * envelope the backend's ``passkey_register_complete`` expects.
 */
export function encodeRegistrationCredential(
  credential: PublicKeyCredential,
): RegistrationCredentialJSON {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: bufferToBase64Url(credential.rawId),
    type: credential.type as "public-key",
    authenticatorAttachment:
      (credential.authenticatorAttachment as AuthenticatorAttachment | null) ??
      null,
    response: {
      clientDataJSON: bufferToBase64Url(response.clientDataJSON),
      attestationObject: bufferToBase64Url(response.attestationObject),
      transports: transportsFromResponse(response),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

/**
 * Serialize a signed assertion credential into the JSON envelope the
 * backend's ``passkey_authenticate_complete`` expects.
 */
export function encodeAuthenticationCredential(
  credential: PublicKeyCredential,
): AuthenticationCredentialJSON {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: bufferToBase64Url(credential.rawId),
    type: credential.type as "public-key",
    authenticatorAttachment:
      (credential.authenticatorAttachment as AuthenticatorAttachment | null) ??
      null,
    response: {
      clientDataJSON: bufferToBase64Url(response.clientDataJSON),
      authenticatorData: bufferToBase64Url(response.authenticatorData),
      signature: bufferToBase64Url(response.signature),
      userHandle: response.userHandle
        ? bufferToBase64Url(response.userHandle)
        : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

/**
 * True when the runtime exposes the WebAuthn API. Guards both
 * server-side rendering and pre-2019 browsers.
 */
export function isWebAuthnSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof navigator.credentials?.create === "function" &&
    typeof navigator.credentials?.get === "function"
  );
}
