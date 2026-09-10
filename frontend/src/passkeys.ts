/**
 * Passkeys in the browser.
 *
 * The whole cryptography happens in the device; what is here is the translation between two
 * shapes of the same thing: the server speaks base64url in JSON, `navigator.credentials`
 * speaks `ArrayBuffer`. Get that wrong and the browser refuses with a message that says
 * nothing about what is actually missing, which is why it stands in one place.
 *
 * `toJSON()` exists on newer browsers and does exactly this. It is used where it is there,
 * and the manual way is what the others get — the fields are the same either way.
 */

export function passkeysPossible(): boolean {
  return typeof window !== "undefined"
    && !!window.PublicKeyCredential
    && !!navigator.credentials?.create;
}

/** base64url → bytes. Padding is optional in base64url and browsers do not add it. */
function bytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/")
    + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** bytes → base64url, the way the server reads them back. */
function text(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  const raw = new Uint8Array(buffer);
  let s = "";
  for (const b of raw) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type Options = Record<string, any>;

/** The server's offer, with its base64url fields turned into buffers. */
function forBrowser(options: Options, kind: "create" | "get"): Options {
  const out: Options = { ...options, challenge: bytes(options.challenge) };
  if (kind === "create") {
    out.user = { ...options.user, id: bytes(options.user.id) };
    out.excludeCredentials = (options.excludeCredentials || [])
      .map((c: Options) => ({ ...c, id: bytes(c.id) }));
  } else {
    out.allowCredentials = (options.allowCredentials || [])
      .map((c: Options) => ({ ...c, id: bytes(c.id) }));
  }
  return out;
}

/** What the browser gave back, in the shape the server verifies. */
function forServer(credential: PublicKeyCredential): Options {
  const anyway = credential as any;
  if (typeof anyway.toJSON === "function") return anyway.toJSON();
  const answer = credential.response as any;
  const out: Options = {
    id: credential.id,
    rawId: text(credential.rawId),
    type: credential.type,
    authenticatorAttachment: anyway.authenticatorAttachment ?? null,
    clientExtensionResults: credential.getClientExtensionResults?.() ?? {},
    response: { clientDataJSON: text(answer.clientDataJSON) },
  };
  if (answer.attestationObject) {
    out.response.attestationObject = text(answer.attestationObject);
    out.response.transports = answer.getTransports?.() ?? [];
  } else {
    out.response.authenticatorData = text(answer.authenticatorData);
    out.response.signature = text(answer.signature);
    out.response.userHandle = answer.userHandle ? text(answer.userHandle) : null;
  }
  return out;
}

export async function makePasskey(options: Options): Promise<Options> {
  const made = await navigator.credentials.create({
    publicKey: forBrowser(options, "create") as PublicKeyCredentialCreationOptions });
  if (!made) throw new Error("no passkey was made");
  return forServer(made as PublicKeyCredential);
}

export async function usePasskey(options: Options): Promise<Options> {
  const signed = await navigator.credentials.get({
    publicKey: forBrowser(options, "get") as PublicKeyCredentialRequestOptions });
  if (!signed) throw new Error("no passkey answered");
  return forServer(signed as PublicKeyCredential);
}

/** Where this key lives, as the browser says it. Only a hint for the list. */
export function whereItLives(credential: Options): string {
  return credential.authenticatorAttachment === "platform" ? "platform"
    : credential.authenticatorAttachment === "cross-platform" ? "cross-platform" : "";
}
