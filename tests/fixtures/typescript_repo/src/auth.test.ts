import { AuthManager } from "./auth";
import { TokenStore } from "./tokenStore";

describe("AuthManager", () => {
  let tokenStore: TokenStore;
  let authManager: AuthManager;

  beforeEach(() => {
    tokenStore = new TokenStore();
    authManager = new AuthManager(tokenStore);
  });

  it("should store token on authenticate", async () => {
    const token = await authManager.authenticate("alice", "password");
    expect(tokenStore.get("alice")).toBe(token);
  });

  it("should remove token on logout", async () => {
    await authManager.authenticate("alice", "password");
    await authManager.logout("alice");
    expect(tokenStore.get("alice")).toBeUndefined();
  });
});
