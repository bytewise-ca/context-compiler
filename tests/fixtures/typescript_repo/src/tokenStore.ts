export class TokenStore {
  private store = new Map<string, string>();

  save(username: string, token: string): void {
    this.store.set(username, token);
  }

  get(username: string): string | undefined {
    return this.store.get(username);
  }

  remove(username: string): void {
    this.store.delete(username);
  }
}
