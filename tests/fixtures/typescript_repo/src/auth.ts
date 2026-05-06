import { TokenStore } from "./tokenStore";

export class AuthManager {
  constructor(private tokenStore: TokenStore) {}

  async authenticate(username: string, password: string): Promise<string> {
    const token = await this.generateToken(username);
    this.tokenStore.save(username, token);
    return token;
  }

  private async generateToken(username: string): Promise<string> {
    return Buffer.from(`${username}:${Date.now()}`).toString("base64");
  }

  async logout(username: string): Promise<void> {
    this.tokenStore.remove(username);
  }
}
