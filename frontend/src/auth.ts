// Purpose: OIDC (Cognito Hosted UI, Authorization Code + PKCE) via oidc-client-ts. Tokens stay in the browser session only.
import { UserManager, WebStorageStateStore, User } from "oidc-client-ts";

const origin = window.location.origin;
export const userManager = new UserManager({
  authority: import.meta.env.VITE_COGNITO_AUTHORITY,
  client_id: import.meta.env.VITE_COGNITO_CLIENT_ID,
  redirect_uri: `${origin}/callback`,
  post_logout_redirect_uri: `${origin}/`,
  response_type: "code", // authorization-code + PKCE
  scope: "openid email profile underwriting/agent.invoke underwriting/decision.write",
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  automaticSilentRenew: true,
});

export const login = () => userManager.signinRedirect();
export const logout = () => {
  const domain = import.meta.env.VITE_COGNITO_DOMAIN;
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
  userManager.removeUser();
  window.location.href = `https://${domain}/logout?client_id=${clientId}&logout_uri=${encodeURIComponent(origin + "/")}`;
};
export async function currentUser(): Promise<User | null> {
  if (window.location.pathname === "/callback") {
    const u = await userManager.signinRedirectCallback();
    window.history.replaceState({}, "", "/");
    return u;
  }
  const u = await userManager.getUser();
  return u && !u.expired ? u : null;
}
export function groupsOf(u: User): string[] {
  return ((u.profile as Record<string, unknown>)["cognito:groups"] as string[]) ?? [];
}
